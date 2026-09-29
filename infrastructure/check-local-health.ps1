param(
    [string]$ComposeFile = "infrastructure\docker-compose.yml",
    [string]$EnvFile = ".env"
)

$ErrorActionPreference = "Continue"
$failed = [System.Collections.Generic.List[string]]::new()

function Check-Step {
    param([string]$Name, [scriptblock]$Action)
    Write-Host "==> $Name"
    try {
        & $Action
        if ($LASTEXITCODE -ne 0) {
            throw "exit code $LASTEXITCODE"
        }
        Write-Host "    OK"
    } catch {
        $failed.Add(("{0}: {1}" -f $Name, $_.Exception.Message))
        Write-Warning ("{0} failed: {1}" -f $Name, $_.Exception.Message)
    }
}

Check-Step "Docker engine" {
    docker info --format "{{.ServerVersion}} {{.OperatingSystem}}"
}

Check-Step "Windows NVIDIA GPU" {
    nvidia-smi --query-gpu=name,driver_version,memory.used,memory.total --format=csv,noheader
}
Check-Step "Docker CUDA bridge" {
    docker run --rm --gpus all infrastructure-geoai-worker:latest python -c "import torch; assert torch.cuda.is_available(); print(torch.cuda.get_device_name(0))"
}

Check-Step "Compose services" {
    docker compose --env-file $EnvFile -f $ComposeFile ps
}

Check-Step "Frontend HTTP" {
    $response = Invoke-WebRequest -UseBasicParsing http://127.0.0.1:5173/ -TimeoutSec 5
    if ($response.StatusCode -ne 200) { throw "HTTP $($response.StatusCode)" }
}

Check-Step "Backend health" {
    $response = Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/health -TimeoutSec 5
    if ($response.StatusCode -ne 200) { throw "HTTP $($response.StatusCode)" }
}

Check-Step "SAM-Road v7 health" {
    $health = Invoke-RestMethod http://127.0.0.1:8765/health -TimeoutSec 5
    if ($health.status -ne "ok" -or -not $health.cuda) {
        throw "SAM-Road health/CUDA check failed"
    }
    Write-Host "    $($health.model_version) on $($health.gpu)"
}
Check-Step "GeoAI worker -> SAM-Road" {
    docker compose --env-file $EnvFile -f $ComposeFile exec -T geoai-worker python -c "import os,urllib.request; u=os.environ['SAMROAD_SERVICE_URL'].rstrip('/')+'/health'; r=urllib.request.urlopen(u,timeout=5); assert r.status==200; print(u)"
}

if ($failed.Count -gt 0) {
    Write-Host ""
    $detail = "Local stack health FAILED:" + [Environment]::NewLine + " - " + ($failed -join ([Environment]::NewLine + " - "))
    Write-Error $detail
    Write-Host "If this follows laptop sleep and Docker CUDA says no adapters were found, restart Docker Desktop, then run:"
    Write-Host "docker compose --env-file $EnvFile -f $ComposeFile up -d"
    exit 1
}

Write-Host ""
Write-Host "Local stack health PASSED."
