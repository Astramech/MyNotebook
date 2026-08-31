Dim ws, http, url, isRunning
url = "http://127.0.0.1:8502"
Set ws = CreateObject("WScript.Shell")

isRunning = False
On Error Resume Next
Set http = CreateObject("MSXML2.XMLHTTP")
http.Open "GET", url, False
http.Send
If http.Status = 200 Then isRunning = True
On Error Goto 0

If Not isRunning Then
    ws.Run "cmd /c cd /d ""C:\Users\25679\Desktop\MyNotebook"" && streamlit run app.py --server.port 8502 --server.address 0.0.0.0", 0, False
    WScript.Sleep 3000
End If

ws.Run url, 1, False
