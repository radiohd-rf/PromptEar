using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Forms;

class Launcher
{
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern IntPtr GetModuleHandle(string lpModuleName);

    [DllImport("kernel32.dll", SetLastError = true)]
    static extern IntPtr CreateJobObject(IntPtr lpJobAttributes, string lpName);

    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool AssignProcessToJobObject(IntPtr hJob, IntPtr hProcess);

    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool SetInformationJobObject(IntPtr hJob, int infoType, IntPtr lpJobObjectInfo, int cbJobObjectInfoLength);

    delegate bool EnumWindowCallback(IntPtr hwnd, IntPtr lParam);

    [DllImport("user32.dll")]
    static extern bool EnumWindows(EnumWindowCallback lpEnumFunc, IntPtr lParam);

    [DllImport("user32.dll", CharSet = CharSet.Auto)]
    static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);

    [DllImport("user32.dll", SetLastError = true)]
    static extern IntPtr LoadImage(IntPtr hinst, string lpszName, uint uType, int cxDesired, int cyDesired, uint fuLoad);

    [DllImport("user32.dll", SetLastError = true)]
    static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter, int X, int Y, int cx, int cy, uint uFlags);

    [DllImport("user32.dll", SetLastError = true, EntryPoint = "SetClassLongPtr")]
    static extern IntPtr SetClassLongPtr64(IntPtr hWnd, int nIndex, IntPtr dwNewLong);

    [DllImport("user32.dll", SetLastError = true, EntryPoint = "SetClassLong")]
    static extern IntPtr SetClassLong32(IntPtr hWnd, int nIndex, IntPtr dwNewLong);

    static IntPtr SetClassLong(IntPtr hWnd, int nIndex, IntPtr dwNewLong)
    {
        if (IntPtr.Size == 8)
            return SetClassLongPtr64(hWnd, nIndex, dwNewLong);
        else
            return SetClassLong32(hWnd, nIndex, dwNewLong);
    }

    [StructLayout(LayoutKind.Sequential)]
    struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION
    {
        public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInfo;
        public IO_COUNTERS IoInfo;
        public PROCESS_MEMORY_COUNTERS ProcessMemoryLimitInfo;
        public ulong PeakProcessMemoryUsed;
        public ulong PeakJobMemoryUsed;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct JOBOBJECT_BASIC_LIMIT_INFORMATION
    {
        public long PerProcessUserTimeLimit;
        public long PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize;
        public UIntPtr MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass;
        public uint SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct IO_COUNTERS
    {
        public ulong ReadOperationCount;
        public ulong WriteOperationCount;
        public ulong OtherOperationCount;
        public ulong ReadTransferCount;
        public ulong WriteTransferCount;
        public ulong OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct PROCESS_MEMORY_COUNTERS
    {
        public uint cb;
        public uint PageFaultCount;
        public UIntPtr PeakWorkingSetSize;
        public UIntPtr WorkingSetSize;
        public UIntPtr QuotaPeakPagedPoolUsage;
        public UIntPtr QuotaPagedPoolUsage;
        public UIntPtr QuotaPeakNonPagedPoolUsage;
        public UIntPtr QuotaNonPagedPoolUsage;
        public UIntPtr PagefileUsage;
        public UIntPtr PeakPagefileUsage;
    }

    const uint IMAGE_ICON = 1;
    const int GCL_HICON = -14;
    const int GCL_HICONSM = -34;
    const uint SWP_NOMOVE = 0x0002;
    const uint SWP_NOSIZE = 0x0001;
    const uint SWP_FRAMECHANGED = 0x0020;

    const int JobObjectExtendedLimitInformation = 9;
    const uint JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000;

    static void Main()
    {
        string dir = Path.GetDirectoryName(typeof(Launcher).Assembly.Location);
        string venvPython = Path.Combine(dir, "venv", "Scripts", "python.exe");
        string mainPy = Path.Combine(dir, "main.py");
        string bootstrap = Path.Combine(dir, "bootstrap.bat");
        if (!File.Exists(venvPython))
        {
            Process boot = new Process();
            boot.StartInfo.FileName = "cmd.exe";
            boot.StartInfo.Arguments = "/c call \"" + bootstrap + "\"";
            boot.StartInfo.WorkingDirectory = dir;
            boot.Start();
            boot.WaitForExit();
        }

        if (File.Exists(venvPython))
        {
            IntPtr job = CreateJobObject(IntPtr.Zero, null);
            if (job != IntPtr.Zero)
            {
                JOBOBJECT_EXTENDED_LIMIT_INFORMATION info = new JOBOBJECT_EXTENDED_LIMIT_INFORMATION();
                info.BasicLimitInfo.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
                int size = Marshal.SizeOf(typeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION));
                IntPtr ptr = Marshal.AllocHGlobal(size);
                Marshal.StructureToPtr(info, ptr, false);
                SetInformationJobObject(job, JobObjectExtendedLimitInformation, ptr, size);
                Marshal.FreeHGlobal(ptr);
            }

            Process server = new Process();
            server.StartInfo.FileName = venvPython;
            server.StartInfo.Arguments = "\"" + mainPy + "\"";
            server.StartInfo.WorkingDirectory = dir;
            server.StartInfo.UseShellExecute = false;
            server.StartInfo.CreateNoWindow = true;
            server.StartInfo.RedirectStandardError = true;
            server.StartInfo.RedirectStandardOutput = true;
            server.StartInfo.StandardOutputEncoding = Encoding.UTF8;
            server.StartInfo.StandardErrorEncoding = Encoding.UTF8;
            server.Start();

            if (job != IntPtr.Zero)
                AssignProcessToJobObject(job, server.Handle);

            // Устанавливаем иконку окна (ждём пока окно pywebview создастся)
            Thread t = new Thread(() => SetWindowIcon("PromptEar"));
            t.IsBackground = true;
            t.Start();

            StringBuilder stderr = new StringBuilder();
            server.ErrorDataReceived += (sender, args) =>
            {
                if (args.Data != null)
                    stderr.AppendLine(args.Data);
            };
            server.BeginErrorReadLine();

            server.WaitForExit();

            if (server.ExitCode != 0)
            {
                string errorText = stderr.ToString().Trim();
                string crashLog = Path.Combine(dir, "crash.log");
                if (File.Exists(crashLog))
                {
                    try { errorText = File.ReadAllText(crashLog, Encoding.UTF8); }
                    catch { }
                }
                if (string.IsNullOrEmpty(errorText))
                    errorText = "Неизвестная ошибка. См. crash.log";
                MessageBox.Show(errorText, "PromptEar — ошибка", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }
    }

    static void SetWindowIcon(string title)
    {
        IntPtr foundHwnd = IntPtr.Zero;

        EnumWindowCallback callback = (hwnd, lParam) =>
        {
            StringBuilder sb = new StringBuilder(256);
            GetWindowText(hwnd, sb, sb.Capacity);
            if (sb.ToString().IndexOf(title, StringComparison.OrdinalIgnoreCase) >= 0)
            {
                foundHwnd = hwnd;
                return false;
            }
            return true;
        };

        for (int i = 0; i < 40; i++)
        {
            foundHwnd = IntPtr.Zero;
            EnumWindows(callback, IntPtr.Zero);
            if (foundHwnd != IntPtr.Zero)
            {
                IntPtr icon = LoadImage(GetModuleHandle(null), "MAINICON", IMAGE_ICON, 0, 0, 0);
                if (icon != IntPtr.Zero)
                {
                    SetClassLong(foundHwnd, GCL_HICON, icon);
                    SetClassLong(foundHwnd, GCL_HICONSM, icon);
                    SetWindowPos(foundHwnd, IntPtr.Zero, 0, 0, 0, 0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_FRAMECHANGED);
                }
                return;
            }
            Thread.Sleep(250);
        }
    }
}
