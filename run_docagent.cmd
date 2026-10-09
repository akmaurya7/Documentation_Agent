@echo off
setlocal EnableExtensions

cd /d "%~dp0"
echo Starting DocAgent from %CD%

where docker >nul 2>nul
if errorlevel 1 (
  echo Docker Desktop and the Docker CLI are required.
  exit /b 1
)

if not exist ".env" (
  echo Creating local .env from .env.example...
  copy /y ".env.example" ".env" >nul
  powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$path = '.env'; $lines = Get-Content $path; $webhook = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N'); $admin = [guid]::NewGuid().ToString('N'); $hash = (Get-FileHash 'prompts/docagent_system.md' -Algorithm SHA256).Hash.ToLower(); $lines = $lines -replace '^DOCAGENT_WEBHOOK_SECRET=.*$', ('DOCAGENT_WEBHOOK_SECRET=' + $webhook) -replace '^DOCAGENT_ADMIN_TOKEN=.*$', ('DOCAGENT_ADMIN_TOKEN=' + $admin) -replace '^DOCAGENT_ALLOWED_PROMPT_HASHES=.*$', ('DOCAGENT_ALLOWED_PROMPT_HASHES=[''' + $hash + ''']'); Set-Content -Path $path -Value $lines"
  if errorlevel 1 (
    echo Could not create local settings.
    exit /b 1
  )
  echo Local settings created. Add a model credential to .env for real runs.
)

echo Building and starting API, worker, Redis, and PostgreSQL...
docker compose up --build -d
if errorlevel 1 (
  echo Docker Compose failed. Run "docker compose logs" for details.
  exit /b 1
)

echo Waiting for the API...
for /l %%n in (1,1,30) do (
  powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 http://127.0.0.1:8000/healthz | Out-Null; exit 0 } catch { exit 1 }"
  if not errorlevel 1 goto ready
  timeout /t 2 /nobreak >nul
)
echo API did not become available. Run "docker compose logs docagent" for details.
exit /b 1

:ready
echo DocAgent is running.
echo Dashboard: http://127.0.0.1:8000/dashboard
start "" "http://127.0.0.1:8000/dashboard"
echo.
echo To stop everything: docker compose down
echo To view logs:       docker compose logs -f
endlocal
