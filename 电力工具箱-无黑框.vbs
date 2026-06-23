Option Explicit

Dim shell, fso, launcherDir, scriptPath, pythonwPath, command, waitOnReturn
Dim toolboxRoot, workspaceRoot, bundledPythonw
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

launcherDir = fso.GetParentFolderName(WScript.ScriptFullName)
waitOnReturn = False

Function Quote(ByVal value)
    Quote = Chr(34) & value & Chr(34)
End Function

Function IsToolboxRoot(ByVal folderPath)
    IsToolboxRoot = fso.FileExists(fso.BuildPath(folderPath, "toolbox_launcher.pyw")) _
        And fso.FileExists(fso.BuildPath(fso.BuildPath(folderPath, "toolbox"), "app.py"))
End Function

Function FindInTree(ByVal folderPath)
    Dim folder, subFolder, candidate
    FindInTree = ""
    If Not fso.FolderExists(folderPath) Then Exit Function
    If IsToolboxRoot(folderPath) Then
        FindInTree = fso.BuildPath(folderPath, "toolbox_launcher.pyw")
        Exit Function
    End If
    Set folder = fso.GetFolder(folderPath)
    For Each subFolder In folder.SubFolders
        candidate = FindInTree(subFolder.Path)
        If candidate <> "" Then
            FindInTree = candidate
            Exit Function
        End If
    Next
End Function

Function FindToolboxScript()
    Dim candidates, item, desktop, documents, found
    candidates = Array(launcherDir, fso.GetParentFolderName(launcherDir), shell.CurrentDirectory)
    For Each item In candidates
        If item <> "" Then
            If fso.FolderExists(item) And IsToolboxRoot(item) Then
                FindToolboxScript = fso.BuildPath(item, "toolbox_launcher.pyw")
                Exit Function
            End If
            If fso.FolderExists(item) Then
                found = FindInTree(item)
                If found <> "" Then
                    FindToolboxScript = found
                    Exit Function
                End If
            End If
        End If
    Next

    desktop = shell.ExpandEnvironmentStrings("%USERPROFILE%") & "\Desktop"
    documents = shell.ExpandEnvironmentStrings("%USERPROFILE%") & "\Documents"
    For Each item In Array(desktop, documents)
        found = FindInTree(item)
        If found <> "" Then
            FindToolboxScript = found
            Exit Function
        End If
    Next
    FindToolboxScript = ""
End Function

scriptPath = FindToolboxScript()
If scriptPath = "" Then
    If shell.Environment("PROCESS")("TOOLBOX_SMOKE") = "1" Then WScript.Echo "TOOLBOX_SCRIPT_NOT_FOUND"
    WScript.Quit 1
End If

If shell.Environment("PROCESS")("TOOLBOX_SMOKE") = "1" Then
    WScript.Echo "TOOLBOX_SCRIPT=" & scriptPath
    WScript.Quit 0
End If

toolboxRoot = fso.GetParentFolderName(scriptPath)
workspaceRoot = fso.GetParentFolderName(toolboxRoot)
bundledPythonw = fso.BuildPath(workspaceRoot, "runtime\python311\pythonw.exe")
pythonwPath = shell.ExpandEnvironmentStrings("%USERPROFILE%") & "\AppData\Local\Programs\Python\Python311\pythonw.exe"
If fso.FileExists(bundledPythonw) Then
    command = Quote(bundledPythonw) & " " & Quote(scriptPath)
ElseIf fso.FileExists(pythonwPath) Then
    command = Quote(pythonwPath) & " " & Quote(scriptPath)
Else
    command = "pyw -3.11 " & Quote(scriptPath)
End If

shell.CurrentDirectory = fso.GetParentFolderName(scriptPath)
shell.Run command, 0, waitOnReturn
