param(
  [string[]]$Radiant = @(),
  [string[]]$Dire = @()
)
$payload = @{ radiant = $Radiant; dire = $Dire } | ConvertTo-Json -Depth 4
Invoke-RestMethod -Uri 'http://127.0.0.1:8765/draft' -Method Post -ContentType 'application/json' -Body $payload | ConvertTo-Json -Depth 4
