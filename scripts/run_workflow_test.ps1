$headers = @{ "X-API-KEY" = "change_me_to_a_strong_secret"; "Content-Type" = "application/json" }
$body = @{ query = "test query"; max_results = 5; min_views = 0; min_outlier_score = 0 } | ConvertTo-Json
Write-Output "Calling search_shorts..."
try {
    $resp = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/search_shorts" -Method Post -Headers $headers -Body $body -TimeoutSec 120
} catch {
    Write-Output "search_shorts failed: $($_.Exception.Message)"
    exit 1
}
Write-Output ("Found " + $resp.total + " shorts")
$ids = $resp.shorts | Select-Object -ExpandProperty video_id
foreach ($id in $ids) {
    Write-Output "`nGet transcript for $id"
    $b = @{ video_id = $id } | ConvertTo-Json
    try {
        $tr = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/get_transcript" -Method Post -Headers $headers -Body $b -TimeoutSec 600
        Write-Output ($tr | ConvertTo-Json -Depth 5)
    } catch {
        Write-Output ("get_transcript failed for " + $id + ": " + $_.Exception.Message)
    }
}
Write-Output "`nListing saved transcripts:"
$dir = Join-Path (Get-Location) "logs\transcripts"
if (-Not (Test-Path $dir)) {
    Write-Output "Transcripts dir not found: $dir"
    exit 0
}
Get-ChildItem -Path $dir -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 20 | ForEach-Object { Write-Output $_.FullName; Get-Content -Path $_.FullName -TotalCount 20 -Encoding UTF8; Write-Output '---' }
