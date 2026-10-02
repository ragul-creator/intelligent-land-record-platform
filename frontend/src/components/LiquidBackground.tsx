import { useEffect, useRef } from "react";

type Wake = { x: number; y: number; dx: number; dy: number; born: number; speed: number };

export function LiquidBackground() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    if (typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const canvas = canvasRef.current;
    if (!canvas || typeof WebGLRenderingContext === "undefined") return;
    let gl: WebGLRenderingContext | null = null;
    try {
      gl = canvas.getContext("webgl", { alpha: true, antialias: false, premultipliedAlpha: true });
    } catch {
      return;
    }
    if (!gl) return;

    const vertexSource = `
      attribute vec2 a_position;
      void main(){ gl_Position = vec4(a_position,0.0,1.0); }
    `;
    const fragmentSource = `
      precision mediump float;
      uniform vec2 u_resolution;
      uniform float u_time;
      uniform vec2 u_mouse;
      uniform vec4 u_wake[12];
      uniform vec2 u_dir[12];
      uniform float u_wakeCount;

      float hash(vec2 p){
        p=fract(p*vec2(123.34,456.21));
        p+=dot(p,p+45.32);
        return fract(p.x*p.y);
      }
      float noise(vec2 p){
        vec2 i=floor(p), f=fract(p);
        f=f*f*(3.0-2.0*f);
        return mix(mix(hash(i),hash(i+vec2(1,0)),f.x),
                   mix(hash(i+vec2(0,1)),hash(i+vec2(1,1)),f.x),f.y);
      }
      void main(){
        vec2 uv=gl_FragCoord.xy/u_resolution.xy;
        uv.y=1.0-uv.y;
        float aspect=u_resolution.x/u_resolution.y;
        vec2 p=vec2(uv.x*aspect,uv.y);
        float t=u_time*.001;
        float distortion=
          sin(p.x*15.0+t*1.05)*.010+
          sin(p.y*19.0-t*.86)*.009+
          sin((p.x+p.y)*11.0+t*.63)*.007;
        distortion+=(noise(p*3.1+vec2(t*.08,-t*.045))-.5)*.015;
        float hi=0.0;
        float shadow=0.0;
        for(int i=0;i<12;i++){
          if(float(i)>=u_wakeCount) break;
          vec4 w=u_wake[i];
          vec2 dir=normalize(u_dir[i]+vec2(.00001,0));
          dir.x*=aspect; dir=normalize(dir);
          vec2 side=vec2(-dir.y,dir.x);
          vec2 delta=p-vec2(w.x*aspect,w.y);
          float forward=dot(delta,dir);
          float lateral=dot(delta,side);
          float behind=max(0.0,-forward);
          float fade=exp(-w.z*1.45);
          float lengthWake=.05+w.z*(.22+w.w*.07);
          float widthWake=.015+behind*.42+w.w*.005;
          float body=smoothstep(.008,.045,behind)*
            (1.0-smoothstep(lengthWake*.72,lengthWake,behind))*
            (1.0-smoothstep(widthWake*.72,widthWake,abs(lateral)));
          float phase=behind*96.0-w.z*13.0;
          float cross=abs(lateral)*125.0-behind*18.0-w.z*9.0;
          float wave=sin(phase)*.65+sin(cross)*.35;
          distortion+=wave*body*.020*fade;
          hi+=max(0.0,wave)*body*.22*fade;
          shadow+=max(0.0,-wave)*body*.075*fade;
        }

        vec2 mouse=u_mouse/u_resolution;
        mouse.y=1.0-mouse.y;
        float cursorDist=length((uv-mouse)*vec2(aspect,1.0));
        distortion+=exp(-cursorDist*22.0)*.007;
        float caustic=
          sin((p.x+distortion*2.4)*28.0+t*1.55)*
          sin((p.y-distortion*2.1)*24.0-t*1.18);
        caustic=pow(max(0.0,caustic),4.0);
        float ridge=smoothstep(.002,.020,abs(distortion));
        vec3 deep=vec3(.03,.29,.46);
        vec3 cyan=vec3(.12,.73,.92);
        vec3 foam=vec3(.94,.995,1.0);
        vec3 color=mix(deep,cyan,ridge*1.15);
        color=mix(color,foam,clamp(caustic*.45+hi*.90,0.0,1.0));
        float alpha=clamp(.04+ridge*.08+caustic*.06+(hi*1.20+shadow*1.35)*.28,.04,.38);
        gl_FragColor=vec4(color,alpha);
      }
    `;
    const compile = (type: number, source: string) => {
      const shader = gl.createShader(type);
      if (!shader) return null;
      gl.shaderSource(shader, source);
      gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
        console.error(gl.getShaderInfoLog(shader));
        gl.deleteShader(shader);
        return null;
      }
      return shader;
    };

    const vs = compile(gl.VERTEX_SHADER, vertexSource);
    const fs = compile(gl.FRAGMENT_SHADER, fragmentSource);
    if (!vs || !fs) return;
    const program = gl.createProgram();
    if (!program) return;
    gl.attachShader(program, vs); gl.attachShader(program, fs); gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) return;
    gl.useProgram(program);

    const buffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1,1,-1,-1,1,-1,1,1,-1,1,1]), gl.STATIC_DRAW);
    const pos = gl.getAttribLocation(program, "a_position");
    gl.enableVertexAttribArray(pos);
    gl.vertexAttribPointer(pos, 2, gl.FLOAT, false, 0, 0);

    const resolutionLoc = gl.getUniformLocation(program, "u_resolution");
    const timeLoc = gl.getUniformLocation(program, "u_time");
    const mouseLoc = gl.getUniformLocation(program, "u_mouse");
    const wakeLoc = gl.getUniformLocation(program, "u_wake[0]");
    const dirLoc = gl.getUniformLocation(program, "u_dir[0]");
    const wakeCountLoc = gl.getUniformLocation(program, "u_wakeCount");

    let dpr = 1;
    let mouseX = innerWidth * .5, mouseY = innerHeight * .5;
    let lastX = mouseX, lastY = mouseY, lastTime = performance.now();
    let wakes: Wake[] = [];
    let frameId = 0;

    const resize = () => {
      dpr = Math.min(devicePixelRatio || 1, 1.5);
      canvas.width = Math.max(1, Math.floor(innerWidth * dpr));
      canvas.height = Math.max(1, Math.floor(innerHeight * dpr));
      gl.viewport(0, 0, canvas.width, canvas.height);
    };

    const pointerMove = (event: PointerEvent) => {
      const now = performance.now();
      mouseX = event.clientX; mouseY = event.clientY;
      const dx = mouseX - lastX, dy = mouseY - lastY;
      const dist = Math.hypot(dx, dy), dt = Math.max(8, now - lastTime);
      if (dist > 3) {
        const inv = 1 / dist;
        wakes.push({
          x: mouseX / innerWidth, y: mouseY / innerHeight,
          dx: dx * inv, dy: dy * inv, born: now,
          speed: Math.max(.25, Math.min(2.6, dist / dt)),
        });
        if (wakes.length > 12) wakes.shift();
        lastX = mouseX; lastY = mouseY; lastTime = now;
      }
      document.documentElement.style.setProperty("--pointer-x", mouseX + "px");
      document.documentElement.style.setProperty("--pointer-y", mouseY + "px");
    };

    const frame = (now: number) => {
      wakes = wakes.filter((wake) => now - wake.born < 1150);
      const wakeData = new Float32Array(48);
      const dirData = new Float32Array(24);
      wakes.forEach((wake, index) => {
        wakeData[index * 4] = wake.x;
        wakeData[index * 4 + 1] = wake.y;
        wakeData[index * 4 + 2] = (now - wake.born) / 1000;
        wakeData[index * 4 + 3] = wake.speed;
        dirData[index * 2] = wake.dx;
        dirData[index * 2 + 1] = wake.dy;
      });
      gl.uniform2f(resolutionLoc, canvas.width, canvas.height);
      gl.uniform1f(timeLoc, now);
      gl.uniform2f(mouseLoc, mouseX * dpr, mouseY * dpr);
      gl.uniform4fv(wakeLoc, wakeData);
      gl.uniform2fv(dirLoc, dirData);
      gl.uniform1f(wakeCountLoc, wakes.length);
      gl.drawArrays(gl.TRIANGLES, 0, 6);
      frameId = requestAnimationFrame(frame);
    };
    addEventListener("resize", resize, { passive: true });
    addEventListener("pointermove", pointerMove, { passive: true });
    resize();
    frameId = requestAnimationFrame(frame);

    return () => {
      cancelAnimationFrame(frameId);
      removeEventListener("resize", resize);
      removeEventListener("pointermove", pointerMove);
      gl.deleteProgram(program);
      gl.deleteShader(vs);
      gl.deleteShader(fs);
      if (buffer) gl.deleteBuffer(buffer);
    };
  }, []);

  return (
    <div className="liquid-background" aria-hidden="true">
      <canvas ref={canvasRef} className="liquid-ripple-canvas" />
      <div className="liquid-orb liquid-orb-a" />
      <div className="liquid-orb liquid-orb-b" />
    </div>
  );
}
