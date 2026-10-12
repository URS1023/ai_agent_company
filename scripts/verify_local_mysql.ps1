$ErrorActionPreference = 'Stop'
$listeners = @(Get-NetTCPConnection -LocalPort 3306 -State Listen)
$service = Get-CimInstance Win32_Service -Filter "Name='MySQL80'"
if ($service.State -ne 'Running' -or $service.PathName -notmatch 'mysqld\.exe') {
    throw 'Expected local MySQL80 service is not running'
}
$owners = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
if ($owners.Count -ne 1) { throw 'Ambiguous MySQL listener ownership' }
$owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($owners[0])"
if ($owner.Name -ne 'mysqld.exe' -or ($owner.ProcessId -ne $service.ProcessId -and $owner.ParentProcessId -ne $service.ProcessId)) {
    throw 'Port 3306 is not owned by the local MySQL80 service'
}
[ordered]@{
    verifiedAt = [DateTime]::UtcNow.ToString('o')
    host = '127.0.0.1'
    port = 3306
    database = 'szh_new'
    machine = [System.Net.Dns]::GetHostName()
    listenerPid = $owner.ProcessId
    listenerProcess = $owner.Name
    parentPid = $owner.ParentProcessId
    servicePid = $service.ProcessId
    service = $service.Name
    servicePath = $service.PathName
} | ConvertTo-Json -Compress
