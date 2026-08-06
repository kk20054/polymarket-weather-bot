using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Net.NetworkInformation;
using System.Text;
using System.Threading;
using System.Windows.Forms;

namespace WeatherBotLauncher
{
    internal sealed class LauncherConfig
    {
        public string ProjectRoot = "";
        public string LogDirectory = "";
        public string BackendHealthUrl = "http://127.0.0.1:8765/api/scheduler/status";
        public string SchedulerStartUrl = "http://127.0.0.1:8765/api/scheduler/start";
        public string DashboardUrl = "http://127.0.0.1:5173/";
        public int BackendPort = 8765;
        public int FrontendPort = 5173;
        public int BackendReadyTimeoutSeconds = 120;
        public int FrontendReadyTimeoutSeconds = 90;
        public bool StartScheduler = true;

        public static LauncherConfig Load(string path)
        {
            if (!File.Exists(path))
            {
                throw new FileNotFoundException("找不到启动器配置文件。", path);
            }

            Dictionary<string, string> values = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (string originalLine in File.ReadAllLines(path, Encoding.UTF8))
            {
                string line = originalLine.Trim().Trim('\uFEFF');
                if (line.Length == 0 || line.StartsWith("#", StringComparison.Ordinal))
                {
                    continue;
                }

                int separator = line.IndexOf('=');
                if (separator <= 0)
                {
                    continue;
                }

                values[line.Substring(0, separator).Trim()] = line.Substring(separator + 1).Trim();
            }

            LauncherConfig config = new LauncherConfig();
            config.ProjectRoot = Required(values, "ProjectRoot");
            config.LogDirectory = Value(values, "LogDirectory", Path.Combine(config.ProjectRoot, "logs"));
            config.BackendHealthUrl = Value(values, "BackendHealthUrl", config.BackendHealthUrl);
            config.SchedulerStartUrl = Value(values, "SchedulerStartUrl", config.SchedulerStartUrl);
            config.DashboardUrl = Value(values, "DashboardUrl", config.DashboardUrl);
            config.BackendPort = IntValue(values, "BackendPort", config.BackendPort);
            config.FrontendPort = IntValue(values, "FrontendPort", config.FrontendPort);
            config.BackendReadyTimeoutSeconds = IntValue(
                values,
                "BackendReadyTimeoutSeconds",
                config.BackendReadyTimeoutSeconds
            );
            config.FrontendReadyTimeoutSeconds = IntValue(
                values,
                "FrontendReadyTimeoutSeconds",
                config.FrontendReadyTimeoutSeconds
            );
            config.StartScheduler = BoolValue(values, "StartScheduler", config.StartScheduler);
            return config;
        }

        private static string Required(Dictionary<string, string> values, string key)
        {
            string result;
            if (!values.TryGetValue(key, out result) || string.IsNullOrWhiteSpace(result))
            {
                throw new InvalidDataException("启动器配置缺少 " + key + "。");
            }
            return result;
        }

        private static string Value(Dictionary<string, string> values, string key, string fallback)
        {
            string result;
            return values.TryGetValue(key, out result) && !string.IsNullOrWhiteSpace(result) ? result : fallback;
        }

        private static int IntValue(Dictionary<string, string> values, string key, int fallback)
        {
            string raw;
            int result;
            return values.TryGetValue(key, out raw) && int.TryParse(raw, out result) ? result : fallback;
        }

        private static bool BoolValue(Dictionary<string, string> values, string key, bool fallback)
        {
            string raw;
            bool result;
            return values.TryGetValue(key, out raw) && bool.TryParse(raw, out result) ? result : fallback;
        }
    }

    internal sealed class StatusForm : Form
    {
        private readonly Label statusLabel;

        public StatusForm()
        {
            Text = "WeatherBot 启动器";
            ClientSize = new Size(430, 132);
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            MinimizeBox = false;
            ShowInTaskbar = true;
            StartPosition = FormStartPosition.CenterScreen;
            TopMost = true;
            BackColor = Color.FromArgb(24, 30, 42);

            Label titleLabel = new Label();
            titleLabel.Text = "正在启动天气量化交易平台";
            titleLabel.ForeColor = Color.White;
            titleLabel.Font = new Font("Microsoft YaHei UI", 12F, FontStyle.Bold);
            titleLabel.AutoSize = true;
            titleLabel.Location = new Point(22, 18);
            Controls.Add(titleLabel);

            statusLabel = new Label();
            statusLabel.Text = "正在检查本机环境...";
            statusLabel.ForeColor = Color.FromArgb(166, 180, 202);
            statusLabel.Font = new Font("Microsoft YaHei UI", 9F, FontStyle.Regular);
            statusLabel.AutoEllipsis = true;
            statusLabel.Location = new Point(24, 54);
            statusLabel.Size = new Size(382, 22);
            Controls.Add(statusLabel);

            ProgressBar progress = new ProgressBar();
            progress.Style = ProgressBarStyle.Marquee;
            progress.MarqueeAnimationSpeed = 28;
            progress.Location = new Point(24, 91);
            progress.Size = new Size(382, 12);
            Controls.Add(progress);
        }

        public void SetStatus(string value)
        {
            statusLabel.Text = value;
            statusLabel.Refresh();
            Application.DoEvents();
        }
    }

    internal static class Program
    {
        private static readonly object LogLock = new object();
        private static string launcherLogPath = "";

        [STAThread]
        private static void Main()
        {
            bool ownsMutex;
            using (Mutex mutex = new Mutex(true, "Local\\WeatherBotDesktopLauncher", out ownsMutex))
            {
                if (!ownsMutex)
                {
                    return;
                }

                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                StatusForm status = new StatusForm();
                status.Show();
                Application.DoEvents();

                try
                {
                    string installDirectory = AppDomain.CurrentDomain.BaseDirectory;
                    string configPath = Path.Combine(installDirectory, "WeatherBotLauncher.ini");
                    LauncherConfig config = LauncherConfig.Load(configPath);
                    Directory.CreateDirectory(config.LogDirectory);
                    launcherLogPath = Path.Combine(config.LogDirectory, "launcher.log");

                    Log("Launcher started.");
                    ValidateProject(config);
                    EnsureBackend(config, installDirectory, status);
                    EnsureFrontend(config, installDirectory, status);

                    if (config.StartScheduler)
                    {
                        status.SetStatus("正在启动数据调度器...");
                        string response = Post(config.SchedulerStartUrl, 15000);
                        Log("Scheduler start response: " + Compact(response, 500));
                    }

                    status.SetStatus("服务已就绪，正在打开看板...");
                    OpenBrowser(config.DashboardUrl);
                    Log("Dashboard opened: " + config.DashboardUrl);
                    Thread.Sleep(500);
                    status.Close();
                }
                catch (Exception exception)
                {
                    Log("ERROR: " + exception);
                    status.Close();
                    MessageBox.Show(
                        "WeatherBot 启动失败。\r\n\r\n" + exception.Message + "\r\n\r\n日志：" + launcherLogPath,
                        "WeatherBot 启动器",
                        MessageBoxButtons.OK,
                        MessageBoxIcon.Error
                    );
                }
            }
        }

        private static void ValidateProject(LauncherConfig config)
        {
            string[] required =
            {
                config.ProjectRoot,
                Path.Combine(config.ProjectRoot, ".venv", "Scripts", "python.exe"),
                Path.Combine(config.ProjectRoot, "dashboard_server.py"),
                Path.Combine(config.ProjectRoot, "frontend", "index.html"),
                Path.Combine(config.ProjectRoot, "frontend", "node_modules", "vite", "bin", "vite.js")
            };

            foreach (string path in required)
            {
                if (!Directory.Exists(path) && !File.Exists(path))
                {
                    throw new FileNotFoundException(
                        "缺少项目运行文件：" + path + "\r\n请先按 README 完成首次安装。"
                    );
                }
            }
        }

        private static void EnsureBackend(LauncherConfig config, string installDirectory, StatusForm status)
        {
            status.SetStatus("正在检查后端服务...");
            if (IsBackendReady(config.BackendHealthUrl))
            {
                Log("Backend already healthy.");
                return;
            }

            if (IsPortListening(config.BackendPort))
            {
                status.SetStatus("检测到后端端口，正在等待健康检查...");
                if (WaitFor(
                    delegate { return IsBackendReady(config.BackendHealthUrl); },
                    15,
                    status,
                    "正在确认端口 8765 上的服务..."
                ))
                {
                    Log("Backend became healthy while waiting.");
                    return;
                }

                throw new InvalidOperationException(
                    "端口 " + config.BackendPort + " 已被占用，但不是可用的 WeatherBot 后端。"
                );
            }

            status.SetStatus("正在启动后端（首次可能需要几十秒）...");
            string python = Path.Combine(config.ProjectRoot, ".venv", "Scripts", "python.exe");
            string logPath = Path.Combine(config.LogDirectory, "backend.log");
            string batchPath = Path.Combine(installDirectory, "runtime", "start-backend.cmd");
            string command = Quote(python)
                + " -m uvicorn dashboard_server:app --host 127.0.0.1 --port "
                + config.BackendPort;
            int pid = StartBatch(batchPath, config.ProjectRoot, command, logPath);
            Log("Backend wrapper started, pid=" + pid + ".");

            if (!WaitFor(
                delegate { return IsBackendReady(config.BackendHealthUrl); },
                config.BackendReadyTimeoutSeconds,
                status,
                "后端正在初始化..."
            ))
            {
                throw new TimeoutException(
                    "后端未在 " + config.BackendReadyTimeoutSeconds + " 秒内就绪。请查看 " + logPath
                );
            }
        }

        private static void EnsureFrontend(LauncherConfig config, string installDirectory, StatusForm status)
        {
            status.SetStatus("正在检查前端服务...");
            if (IsFrontendReady(config.DashboardUrl))
            {
                Log("Frontend already healthy.");
                return;
            }

            if (IsPortListening(config.FrontendPort))
            {
                status.SetStatus("检测到前端端口，正在等待页面就绪...");
                if (WaitFor(
                    delegate { return IsFrontendReady(config.DashboardUrl); },
                    10,
                    status,
                    "正在确认端口 5173 上的页面..."
                ))
                {
                    Log("Frontend became healthy while waiting.");
                    return;
                }

                throw new InvalidOperationException(
                    "端口 " + config.FrontendPort + " 已被占用，但不是 WeatherBot 看板。"
                );
            }

            status.SetStatus("正在启动前端看板...");
            string node = FindOnPath("node.exe");
            if (string.IsNullOrWhiteSpace(node))
            {
                throw new FileNotFoundException("找不到 node.exe，请先安装 Node.js。");
            }

            string frontendRoot = Path.Combine(config.ProjectRoot, "frontend");
            string vite = Path.Combine(frontendRoot, "node_modules", "vite", "bin", "vite.js");
            string logPath = Path.Combine(config.LogDirectory, "frontend.log");
            string batchPath = Path.Combine(installDirectory, "runtime", "start-frontend.cmd");
            string command = Quote(node)
                + " "
                + Quote(vite)
                + " --host 127.0.0.1 --port "
                + config.FrontendPort
                + " --strictPort";
            int pid = StartBatch(batchPath, frontendRoot, command, logPath);
            Log("Frontend wrapper started, pid=" + pid + ".");

            if (!WaitFor(
                delegate { return IsFrontendReady(config.DashboardUrl); },
                config.FrontendReadyTimeoutSeconds,
                status,
                "前端正在编译..."
            ))
            {
                throw new TimeoutException(
                    "前端未在 " + config.FrontendReadyTimeoutSeconds + " 秒内就绪。请查看 " + logPath
                );
            }
        }

        private static int StartBatch(
            string batchPath,
            string workingDirectory,
            string command,
            string outputLogPath
        )
        {
            Directory.CreateDirectory(Path.GetDirectoryName(batchPath));
            Directory.CreateDirectory(Path.GetDirectoryName(outputLogPath));
            string[] lines =
            {
                "@echo off",
                "cd /d " + Quote(workingDirectory),
                "echo.>> " + Quote(outputLogPath),
                "echo ==== WeatherBot start %date% %time% ====>> " + Quote(outputLogPath),
                command + " >> " + Quote(outputLogPath) + " 2>&1"
            };
            File.WriteAllLines(batchPath, lines, Encoding.Default);

            ProcessStartInfo info = new ProcessStartInfo();
            info.FileName = Environment.GetEnvironmentVariable("ComSpec") ?? "cmd.exe";
            info.Arguments = "/d /s /c \"\"" + batchPath + "\"\"";
            info.WorkingDirectory = workingDirectory;
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            info.WindowStyle = ProcessWindowStyle.Hidden;

            Process process = Process.Start(info);
            if (process == null)
            {
                throw new InvalidOperationException("无法启动后台进程：" + batchPath);
            }
            return process.Id;
        }

        private static bool WaitFor(
            Func<bool> check,
            int timeoutSeconds,
            StatusForm status,
            string message
        )
        {
            DateTime deadline = DateTime.UtcNow.AddSeconds(timeoutSeconds);
            while (DateTime.UtcNow < deadline)
            {
                if (check())
                {
                    return true;
                }
                status.SetStatus(message);
                Thread.Sleep(500);
            }
            return check();
        }

        private static bool IsBackendReady(string url)
        {
            string body;
            return TryGet(url, 3000, out body);
        }

        private static bool IsFrontendReady(string url)
        {
            string body;
            return TryGet(url, 3000, out body)
                && body.IndexOf("WeatherBot", StringComparison.OrdinalIgnoreCase) >= 0
                && body.IndexOf("id=\"root\"", StringComparison.OrdinalIgnoreCase) >= 0;
        }

        private static bool TryGet(string url, int timeoutMilliseconds, out string body)
        {
            body = "";
            try
            {
                HttpWebRequest request = (HttpWebRequest)WebRequest.Create(url);
                request.Method = "GET";
                request.Proxy = null;
                request.Timeout = timeoutMilliseconds;
                request.ReadWriteTimeout = timeoutMilliseconds;
                request.KeepAlive = false;
                using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
                using (StreamReader reader = new StreamReader(response.GetResponseStream()))
                {
                    body = reader.ReadToEnd();
                    return response.StatusCode == HttpStatusCode.OK;
                }
            }
            catch
            {
                return false;
            }
        }

        private static string Post(string url, int timeoutMilliseconds)
        {
            HttpWebRequest request = (HttpWebRequest)WebRequest.Create(url);
            request.Method = "POST";
            request.Proxy = null;
            request.Timeout = timeoutMilliseconds;
            request.ReadWriteTimeout = timeoutMilliseconds;
            request.KeepAlive = false;
            request.ContentLength = 0;
            using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
            using (StreamReader reader = new StreamReader(response.GetResponseStream()))
            {
                if (response.StatusCode != HttpStatusCode.OK)
                {
                    throw new WebException("调度器启动接口返回 " + (int)response.StatusCode + "。");
                }
                return reader.ReadToEnd();
            }
        }

        private static bool IsPortListening(int port)
        {
            try
            {
                foreach (System.Net.IPEndPoint endpoint in IPGlobalProperties.GetIPGlobalProperties().GetActiveTcpListeners())
                {
                    if (endpoint.Port == port)
                    {
                        return true;
                    }
                }
            }
            catch
            {
                // The health check remains the authoritative test.
            }
            return false;
        }

        private static string FindOnPath(string fileName)
        {
            string path = Environment.GetEnvironmentVariable("PATH") ?? "";
            foreach (string segment in path.Split(Path.PathSeparator))
            {
                string directory = segment.Trim().Trim('"');
                if (directory.Length == 0)
                {
                    continue;
                }
                string candidate = Path.Combine(directory, fileName);
                if (File.Exists(candidate))
                {
                    return candidate;
                }
            }
            return "";
        }

        private static void OpenBrowser(string url)
        {
            ProcessStartInfo info = new ProcessStartInfo();
            info.FileName = url;
            info.UseShellExecute = true;
            Process.Start(info);
        }

        private static string Quote(string value)
        {
            return "\"" + value.Replace("\"", "\"\"") + "\"";
        }

        private static string Compact(string value, int maxLength)
        {
            string compact = (value ?? "").Replace("\r", " ").Replace("\n", " ");
            return compact.Length <= maxLength ? compact : compact.Substring(0, maxLength);
        }

        private static void Log(string message)
        {
            if (string.IsNullOrWhiteSpace(launcherLogPath))
            {
                return;
            }
            lock (LogLock)
            {
                File.AppendAllText(
                    launcherLogPath,
                    DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss.fff") + " " + message + Environment.NewLine,
                    Encoding.UTF8
                );
            }
        }
    }
}
