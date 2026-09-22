import { useEffect, useRef, useState } from "react";
import Globe from "react-globe.gl";
import "./EarthBackground.css";

export function EarthBackground() {
  const wrapperRef = useRef<HTMLDivElement>(null);
  const globeRef = useRef<any>(null);

  const [countries, setCountries] = useState<any[]>([]);
  const [hoveredCountry, setHoveredCountry] = useState<any>(null);
  const fixedSize = { width: 720, height: 620 };

  
  useEffect(() => {
    let cancelled = false;

    fetch("/data/world-countries.geojson")
      .then((response) => {
        if (!response.ok) {
          throw new Error("Unable to load world map.");
        }

        return response.json();
      })
      .then((data) => {
        if (!cancelled) {
          setCountries(data.features ?? []);
        }
      })
      .catch((error) => {
        console.warn("Earth map unavailable:", error);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  

  useEffect(() => {
    const globe = globeRef.current;

    if (!globe) return;

    const controls = globe.controls?.();

    if (controls) {
      controls.autoRotate = true;
      controls.autoRotateSpeed = 0.65;
      controls.enableDamping = true;
      controls.dampingFactor = 0.08;
      controls.enablePan = false;
      controls.enableZoom = true;
      controls.minDistance = 150;
      controls.maxDistance = 450;
    }

    globe.pointOfView?.(
      {
        lat: 20,
        lng: 78,
        altitude: 1.85,
      },
      0,
    );

    const material = globe.globeMaterial?.() as any;

    if (material) {
      material.color?.set?.("#dceae4");
      material.emissive?.set?.("#edf4f0");
      material.emissiveIntensity = 0.18;
      material.shininess = 12;
      material.transparent = true;
      material.opacity = 0.88;
      material.needsUpdate = true;
    }
  }, [countries]);

  return (
    <div
      ref={wrapperRef}
      className="earth-background"
      aria-hidden="true"
    >
      <div className="earth-halo" />

      <div className="earth-interaction-hint">
        <span>●</span>
        Drag to rotate · scroll to zoom
      </div>

      <Globe
        ref={globeRef}
        width={fixedSize.width}
        height={fixedSize.height}
        backgroundColor="rgba(0,0,0,0)"
        globeImageUrl="//unpkg.com/three-globe/example/img/earth-blue-marble.jpg"
        bumpImageUrl="//unpkg.com/three-globe/example/img/earth-topology.png"
        showGlobe
        showGraticules
        showAtmosphere
        atmosphereColor="#74aa9b"
        atmosphereAltitude={0.13}
        animateIn
        enablePointerInteraction
        polygonsData={countries}
        polygonAltitude={(country: any) =>
          country === hoveredCountry ? 0.022 : 0.008
        }
        polygonCapColor={(country: any) =>
          country === hoveredCountry
            ? "rgba(67, 160, 125, 0.22)"
            : "rgba(255, 255, 255, 0.015)"
        }
        polygonSideColor={() => "rgba(255,255,255,0.01)"}
        polygonStrokeColor={() => "rgba(236, 255, 247, 0.34)"}
        polygonLabel={(country: any) =>
          country?.properties?.ADMIN ??
          country?.properties?.NAME ??
          ""
        }
        polygonsTransitionDuration={220}
        onPolygonHover={(country: any) =>
          setHoveredCountry(country ?? null)
        }
      />
    </div>
  );
}







