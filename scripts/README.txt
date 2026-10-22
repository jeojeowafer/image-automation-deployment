Install-Module powershell-yaml -Scope CurrentUser

.\deploy.ps1 `
    -Customer "aspect-prober" `
    -AppVersion "1.5.0" `
    -AppCommit "a13f8c2" `
    -SourceExe ".\app\main.exe" `
    -SourceCustomerDirectory ".\configs\aspect-prober" `
    -TargetDirectory ".\deployments\aspect-prober" `
    -ConfigVersion "2.1.0" `
    -ConfigCommit "b24d9e1" `
    -MaskVersion "1.0.0" `
    -MaskCommit "c35e0f2"