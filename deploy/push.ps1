# Ship the committed HEAD to the VM and run setup.sh there. Run from the repo root on Windows:
#   powershell -File deploy/push.ps1 -Server ubuntu@203.0.113.7 -Domain 203-0-113-7.sslip.io -Key $HOME\.ssh\oci.key
param(
    [Parameter(Mandatory)] [string]$Server,
    [Parameter(Mandatory)] [string]$Domain,
    [string]$Key = "$HOME\.ssh\id_ed25519"
)
$ErrorActionPreference = "Stop"
$tar = Join-Path $env:TEMP "floodwatch-release.tar.gz"
git archive --format=tar.gz -o $tar HEAD   # committed code only: no .env, no data/*.db
scp -i $Key $tar deploy/setup.sh deploy/floodwatch.service deploy/Caddyfile deploy/floodwatch.env.example "${Server}:/tmp/"
ssh -i $Key $Server "sudo bash /tmp/setup.sh /tmp/floodwatch-release.tar.gz $Domain"
