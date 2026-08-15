using System;
using System.Collections.Concurrent;
using System.IO;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using UnityEngine;

public class PythonWorldClient : MonoBehaviour
{
    [Header("Python Bridge")]
    public string host = "127.0.0.1";
    public int port = 8765;

    private TcpClient client;
    private NetworkStream stream;

    private CancellationTokenSource cancellationTokenSource;

    private readonly ConcurrentQueue<string> incomingMessages =
        new ConcurrentQueue<string>();

    private bool connected;

    async void Start()
    {
        await ConnectToPython();
    }

    async Task ConnectToPython()
    {
        cancellationTokenSource = new CancellationTokenSource();

        while (!cancellationTokenSource.IsCancellationRequested)
        {
            try
            {
                Debug.Log($"[PythonWorldClient] Connecting to {host}:{port}");

                client = new TcpClient();

                await client.ConnectAsync(host, port);

                stream = client.GetStream();

                connected = true;

                Debug.Log("[PythonWorldClient] Connected to Python");

                _ = ReceiveLoop(
                    cancellationTokenSource.Token
                );

                return;
            }
            catch (Exception exception)
            {
                connected = false;

                Debug.LogWarning(
                    $"[PythonWorldClient] Connection failed: {exception.Message}"
                );

                await Task.Delay(2000);
            }
        }
    }

    async Task ReceiveLoop(
        CancellationToken cancellationToken
    )
    {
        byte[] buffer = new byte[65536];

        StringBuilder receiveBuffer =
            new StringBuilder();

        try
        {
            while (
                connected &&
                !cancellationToken.IsCancellationRequested
            )
            {
                int bytesRead = await stream.ReadAsync(
                    buffer,
                    0,
                    buffer.Length,
                    cancellationToken
                );

                if (bytesRead == 0)
                {
                    break;
                }

                string text = Encoding.UTF8.GetString(
                    buffer,
                    0,
                    bytesRead
                );

                receiveBuffer.Append(text);

                ProcessReceiveBuffer(receiveBuffer);
            }
        }
        catch (Exception exception)
        {
            if (!cancellationToken.IsCancellationRequested)
            {
                Debug.LogWarning(
                    $"[PythonWorldClient] Receive error: {exception.Message}"
                );
            }
        }

        connected = false;

        Debug.LogWarning(
            "[PythonWorldClient] Python disconnected"
        );
    }

    void ProcessReceiveBuffer(
        StringBuilder receiveBuffer
    )
    {
        while (true)
        {
            string current = receiveBuffer.ToString();

            int newlineIndex = current.IndexOf('\n');

            if (newlineIndex < 0)
            {
                return;
            }

            string message = current.Substring(
                0,
                newlineIndex
            ).Trim();

            receiveBuffer.Remove(
                0,
                newlineIndex + 1
            );

            if (!string.IsNullOrWhiteSpace(message))
            {
                incomingMessages.Enqueue(message);
            }
        }
    }

    void Update()
    {
        while (
            incomingMessages.TryDequeue(
                out string message
            )
        )
        {
            HandleMessage(message);
        }
    }

    void HandleMessage(string json)
    {
        Debug.Log(
            $"[Python -> Unity] {json}"
        );
    }

    void OnDestroy()
    {
        Disconnect();
    }

    void OnApplicationQuit()
    {
        Disconnect();
    }

    void Disconnect()
    {
        connected = false;

        cancellationTokenSource?.Cancel();

        try
        {
            stream?.Close();
        }
        catch { }

        try
        {
            client?.Close();
        }
        catch { }

        stream = null;
        client = null;

        Debug.Log(
            "[PythonWorldClient] Connection closed"
        );
    }
}