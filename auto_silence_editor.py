import urllib.request
import hashlib
#!/usr/bin/env python3
"""
EZVideo Cut - USB Destekli, GPU Hızlandırmalı, Uzaktan Versiyon & Telemetri Korumalı Toplu Video Sessizlik Kesici
- Render Sırasında Tam Kilit: Ayarlar, çıktı klasörü, sürükle-bırak ve dosya ekleme butonları kilitlenir.
- Güvenli İptal: İptal Et'e basıldığında onay pop-up'ı (Evet/Hayır) sorulur.
- Sadece Duraklat ve İptal Et Butonları aktif kalır.
- USB / Harici Disk Uyumlu: Yavaş USB okuma hızlarında donma veya kilitlenme yapmaz.
- Resmi TkinterDnD2 Desteği: Çökmeyen, hafıza korumalı OLE sürükle-bırak motoru.
- Sıfır Donma (Zero-Lag Kuyruk): Tüm analiz ve okuma işlemleri arka planda sırayla yapılır.
- Windows CREATE_NO_WINDOW: Gizli konsol pencereleri oluşturulmaz.
- Taşınabilir (Portable): EXE yanında veya PyInstaller içinde bulunan ffmpeg.exe'yi otomatik tanır.
"""

import os
import sys
import re
import json
import uuid
import shutil
import argparse
import subprocess
import platform
import time
import signal
import queue
from typing import List, Tuple, Dict, Any, Optional

# Resmi TkinterDnD2 Sürükle-Bırak Motoru (Çökme Korumalı)
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    HAS_TKDND = True
except ImportError:
    HAS_TKDND = False

# Windows için konsol penceresi oluşturmama bayrağı
CREATE_NO_WINDOW = 0x08000000 if platform.system() == "Windows" else 0

# ==============================================================================
# 1. TAŞINABİLİR BİNARY BULUCU (FFmpeg / FFprobe)
# ==============================================================================

def get_binary_path(name: str) -> str:
    """FFmpeg ve FFprobe ikili dosyalarını EXE yanında, PyInstaller içinde veya sistemde arar."""
    ext = ".exe" if platform.system() == "Windows" else ""
    target_name = f"{name}{ext}"
    
    # 1. PyInstaller geçici açılış dizini
    if hasattr(sys, "_MEIPASS"):
        p = os.path.join(sys._MEIPASS, target_name)
        if os.path.exists(p):
            return p
            
    # 2. .exe veya Python dosyasının bulunduğu klasör
    base_dir = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
    p = os.path.join(base_dir, target_name)
    if os.path.exists(p):
        return p
        
    # 3. Sistem PATH ortam değişkenleri
    found = shutil.which(name)
    return found if found else target_name

FFMPEG_BIN = get_binary_path("ffmpeg")
FFPROBE_BIN = get_binary_path("ffprobe")

def check_ffmpeg() -> bool:
    """FFmpeg ve FFprobe'un erişilebilir olduğunu doğrular."""
    try:
        r1 = subprocess.run([FFMPEG_BIN, "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=CREATE_NO_WINDOW)
        r2 = subprocess.run([FFPROBE_BIN, "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=CREATE_NO_WINDOW)
        return (r1.returncode == 0) and (r2.returncode == 0)
    except Exception:
        return False

# ==============================================================================
# 2. PROCESS CONTROLLER (DURAKLATMA / DEVAM ETTİRME / İPTAL ETME)
# ==============================================================================


# ==============================================================================
# EZVideo Cut - Versiyon & Telemetri Yönetimi (GitHub Destekli)
# ==============================================================================
APP_NAME = "EZVideo Cut"
APP_VERSION = "1.0.0"
GITHUB_RAW_VERSION_URL = "https://raw.githubusercontent.com/Musileno/ezvideocut/main/version.json"

class RemoteVersionManager:
    """GitHub üzerinden uzaktan versiyon kontrolü, duyuru ve limit kontrolü yapar."""
    def __init__(self, current_version: str = APP_VERSION):
        self.current_version = current_version
        self.min_supported_version = "1.0.0"
        self.latest_version = "1.0.0"
        self.daily_limit_free = 0  # 0 = limitsiz
        self.force_update = False
        self.download_url = "https://github.com/Musileno/ezvideocut/releases"
        self.announcement = ""

    def check_remote(self, on_complete_callback=None):
        def _worker():
            try:
                req = urllib.request.Request(
                    GITHUB_RAW_VERSION_URL,
                    headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"}
                )
                with urllib.request.urlopen(req, timeout=4) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode())
                        self.min_supported_version = data.get("min_supported_version", "1.0.0")
                        self.latest_version = data.get("latest_version", "1.0.0")
                        self.daily_limit_free = data.get("daily_limit_free", 0)
                        self.force_update = data.get("force_update", False)
                        self.download_url = data.get("download_url", self.download_url)
                        self.announcement = data.get("announcement", "")
            except Exception:
                pass
            
            if on_complete_callback:
                on_complete_callback(self)

        threading.Thread(target=_worker, daemon=True).start()

class TelemetryTracker:
    """Programın kaç farklı kullanıcıda açıldığını ve kaç video kesildiğini anonim olarak sayar."""
    @staticmethod
    def get_anonymous_device_id() -> str:
        try:
            raw = f"{uuid.getnode()}_{platform.node()}_{platform.system()}"
            return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        except Exception:
            return "unknown_device"

    @classmethod
    def track_event(cls, event_name: str, count: int = 1):
        def _worker():
            try:
                device_id = cls.get_anonymous_device_id()
                hit_url = f"https://hits.seeyoufarm.com/api/count/incr/badge.svg?url=https://github.com/Musileno/ezvideocut-telemetry/{event_name}/{device_id}"
                req = urllib.request.Request(hit_url, headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"})
                urllib.request.urlopen(req, timeout=3)
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True).start()

class ProcessController:
    """FFmpeg sürecini güvenli şekilde duraklatır, devam ettirir veya iptal eder."""
    def __init__(self):
        self.process: Optional[subprocess.Popen] = None
        self.is_paused: bool = False
        self.is_cancelled: bool = False

    def attach(self, proc: subprocess.Popen):
        self.process = proc
        self.is_paused = False
        self.is_cancelled = False

    def suspend(self) -> bool:
        if not self.process or self.process.poll() is not None:
            return False
        if self.is_paused:
            return True
            
        system = platform.system()
        try:
            if system == "Windows":
                import ctypes
                PROCESS_SUSPEND_RESUME = 0x0800
                handle = ctypes.windll.kernel32.OpenProcess(PROCESS_SUSPEND_RESUME, False, self.process.pid)
                if handle:
                    ctypes.windll.ntdll.NtSuspendProcess(handle)
                    ctypes.windll.kernel32.CloseHandle(handle)
                    self.is_paused = True
                    return True
            else:
                os.kill(self.process.pid, signal.SIGSTOP)
                self.is_paused = True
                return True
        except Exception:
            pass
        return False

    def resume(self) -> bool:
        if not self.process or self.process.poll() is not None:
            return False
        if not self.is_paused:
            return True
            
        system = platform.system()
        try:
            if system == "Windows":
                import ctypes
                PROCESS_SUSPEND_RESUME = 0x0800
                handle = ctypes.windll.kernel32.OpenProcess(PROCESS_SUSPEND_RESUME, False, self.process.pid)
                if handle:
                    ctypes.windll.ntdll.NtResumeProcess(handle)
                    ctypes.windll.kernel32.CloseHandle(handle)
                    self.is_paused = False
                    return True
            else:
                os.kill(self.process.pid, signal.SIGCONT)
                self.is_paused = False
                return True
        except Exception:
            pass
        return False

    def cancel(self):
        self.is_cancelled = True
        if self.is_paused:
            self.resume()
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.wait(timeout=1.0)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass

# ==============================================================================
# 3. YARDIMCI FONKSİYONLAR & DONANIM TESPİTİ
# ==============================================================================

def open_output_folder(target_path: str):
    """İşlem bittiğinde çıktı klasörünü açar."""
    try:
        norm_path = os.path.abspath(target_path)
        folder = norm_path if os.path.isdir(norm_path) else os.path.dirname(norm_path)
        
        system = platform.system()
        if system == "Windows":
            if os.path.isfile(norm_path):
                subprocess.Popen(f'explorer /select,"{norm_path}"', creationflags=CREATE_NO_WINDOW)
            else:
                os.startfile(folder)
        elif system == "Darwin":
            if os.path.isfile(norm_path):
                subprocess.Popen(["open", "-R", norm_path])
            else:
                subprocess.Popen(["open", folder])
        else:
            subprocess.Popen(["xdg-open", folder])
    except Exception:
        pass

def get_media_info(filepath: str) -> Dict[str, Any]:
    """Video meta verilerini USB veya disk üzerinden güvenle çıkarır."""
    cmd = [
        FFPROBE_BIN, "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,duration,codec_name",
        "-show_entries", "format=duration,size,bit_rate",
        "-of", "json",
        filepath
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True, creationflags=CREATE_NO_WINDOW)
    data = json.loads(res.stdout)
    
    stream = data.get("streams", [{}])[0] if data.get("streams") else {}
    fmt = data.get("format", {})
    
    width = int(stream.get("width", 1920))
    height = int(stream.get("height", 1080))
    
    fps_str = stream.get("r_frame_rate", "30/1")
    if "/" in fps_str:
        num, den = fps_str.split("/")
        fps = float(num) / float(den) if float(den) != 0 else 30.0
    else:
        fps = float(fps_str)
        
    duration = float(fmt.get("duration", stream.get("duration", 0.0)))
    
    return {
        "filepath": os.path.abspath(filepath),
        "filename": os.path.basename(filepath),
        "width": width,
        "height": height,
        "fps": round(fps, 3),
        "duration": duration,
        "codec": stream.get("codec_name", "h264")
    }

def test_hardware_encoder(encoder_name: str) -> bool:
    """GPU enkoderinin aktif çalışıp çalışmadığını test eder."""
    cmd = [
        FFMPEG_BIN, "-y",
        "-f", "lavfi", "-i", "nullsrc=s=64x64:d=0.05",
        "-c:v", encoder_name,
        "-f", "null", "-"
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
        return res.returncode == 0
    except Exception:
        return False

def detect_best_encoder() -> Tuple[str, List[str], str]:
    """Sistemdeki en hızlı GPU motorunu belirler."""
    if test_hardware_encoder("h264_nvenc"):
        return "h264_nvenc", ["-c:v", "h264_nvenc", "-preset", "p1", "-tune", "ll", "-rc", "vbr", "-cq", "20", "-b:v", "0"], "NVIDIA NVENC (GPU)"
    if test_hardware_encoder("h264_qsv"):
        return "h264_qsv", ["-c:v", "h264_qsv", "-global_quality", "20", "-preset", "veryfast"], "Intel QuickSync QSV (GPU)"
    if test_hardware_encoder("h264_amf"):
        return "h264_amf", ["-c:v", "h264_amf", "-quality", "speed", "-rc", "cqp", "-qp_i", "20", "-qp_p", "20"], "AMD AMF (GPU)"
    return "libx264", ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", "-threads", "0"], "CPU Ultrafast (İşlemci)"

# ==============================================================================
# 4. SESSİZLİK TESPİTİ VE ZAMANLAMA (USB TAMPON KORUMALI)
# ==============================================================================

class SilenceDetector:
    def __init__(self, db_threshold: float = -30.0, min_duration: float = 0.5, padding: float = 0.15):
        self.db_threshold = db_threshold
        self.min_duration = min_duration
        self.padding = padding

    def detect_silence_intervals(self, video_path: str, controller: Optional[ProcessController] = None) -> List[Tuple[float, float]]:
        """USB'den sadece ses akışını okuyarak USB hız darboğazını tamamen ortadan kaldırır."""
        cmd = [
            FFMPEG_BIN, "-vn", "-sn", "-dn",
            "-i", video_path,
            "-af", f"silencedetect=noise={self.db_threshold}dB:d={self.min_duration}",
            "-f", "null", "-"
        ]
        proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE, universal_newlines=True, creationflags=CREATE_NO_WINDOW)
        if controller:
            controller.attach(proc)
            
        _, stderr = proc.communicate()
        
        if controller and controller.is_cancelled:
            return []
            
        silence_starts = []
        silence_ends = []
        
        for line in stderr.splitlines():
            if "silence_start:" in line:
                m = re.search(r"silence_start:\s*([0-9.]+)", line)
                if m:
                    silence_starts.append(float(m.group(1)))
            elif "silence_end:" in line:
                m = re.search(r"silence_end:\s*([0-9.]+)", line)
                if m:
                    silence_ends.append(float(m.group(1)))
                    
        intervals = []
        for i in range(min(len(silence_starts), len(silence_ends))):
            intervals.append((silence_starts[i], silence_ends[i]))
            
        if len(silence_starts) > len(silence_ends):
            intervals.append((silence_starts[-1], float('inf')))
            
        return intervals

    def compute_speech_intervals(self, total_duration: float, silence_intervals: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
        if not silence_intervals:
            return [(0.0, total_duration)]
            
        raw_speech = []
        current_pos = 0.0
        
        for s_start, s_end in silence_intervals:
            if s_start > current_pos:
                raw_speech.append((current_pos, min(s_start, total_duration)))
            current_pos = min(s_end, total_duration)
            
        if current_pos < total_duration:
            raw_speech.append((current_pos, total_duration))
            
        if not raw_speech:
            return []
            
        padded_speech = []
        for start, end in raw_speech:
            p_start = max(0.0, start - self.padding)
            p_end = min(total_duration, end + self.padding)
            
            if not padded_speech:
                padded_speech.append((p_start, p_end))
            else:
                prev_start, prev_end = padded_speech[-1]
                if p_start <= prev_end:
                    padded_speech[-1] = (prev_start, max(prev_end, p_end))
                else:
                    padded_speech.append((p_start, p_end))
                    
        return padded_speech

# ==============================================================================
# 5. YÜKSEK HIZLI VİDEO RENDER MOTORU
# ==============================================================================

class VideoRenderer:
    @staticmethod
    def render_fast(video_path: str, intervals: List[Tuple[float, float]], output_path: str, encoder_mode: str = "auto", controller: Optional[ProcessController] = None, log_callback=None, progress_callback=None):
        if not intervals:
            raise ValueError("Kaydedilecek konuşma aralığı bulunamadı.")
            
        target_duration = sum(e - s for s, e in intervals)
        
        if encoder_mode == "auto":
            enc_name, enc_flags, enc_label = detect_best_encoder()
        elif encoder_mode == "nvenc":
            enc_name, enc_flags, enc_label = "h264_nvenc", ["-c:v", "h264_nvenc", "-preset", "p1", "-tune", "ll", "-rc", "vbr", "-cq", "20", "-b:v", "0"], "NVIDIA NVENC (GPU)"
        elif encoder_mode == "qsv":
            enc_name, enc_flags, enc_label = "h264_qsv", ["-c:v", "h264_qsv", "-global_quality", "20", "-preset", "veryfast"], "Intel QuickSync (GPU)"
        elif encoder_mode == "amf":
            enc_name, enc_flags, enc_label = "h264_amf", ["-c:v", "h264_amf", "-quality", "speed", "-rc", "cqp", "-qp_i", "20", "-qp_p", "20"], "AMD AMF (GPU)"
        else:
            enc_name, enc_flags, enc_label = "libx264", ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", "-threads", "0"], "CPU Ultrafast (İşlemci)"
            
        if log_callback:
            log_callback(f"Kullanılan Enkoder: {enc_label}")
            log_callback(f"{len(intervals)} video parçası birleştiriliyor...")
            
        filter_complex = []
        concat_inputs = []
        
        for idx, (start, end) in enumerate(intervals):
            filter_complex.append(f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS[v{idx}];")
            filter_complex.append(f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS[a{idx}];")
            concat_inputs.append(f"[v{idx}][a{idx}]")
            
        filter_complex.append("".join(concat_inputs) + f"concat=n={len(intervals)}:v=1:a=1[outv][outa]")
        
        cmd = [
            FFMPEG_BIN, "-y",
            "-i", video_path,
            "-filter_complex", "".join(filter_complex),
            "-map", "[outv]",
            "-map", "[outa]"
        ] + enc_flags + [
            "-c:a", "aac",
            "-b:a", "192k",
            "-pix_fmt", "yuv420p",
            output_path
        ]
        
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, creationflags=CREATE_NO_WINDOW)
        if controller:
            controller.attach(proc)
            
        last_update = 0.0
        for line in proc.stdout:
            if controller and controller.is_cancelled:
                break
            if "time=" in line:
                now = time.time()
                # Arayüzü kasmamak için en fazla 100ms'de bir güncelle
                if now - last_update >= 0.1:
                    last_update = now
                    m_time = re.search(r"time=(\d+):(\d+):(\d+\.\d+)", line)
                    m_speed = re.search(r"speed=\s*([\d\.]+)x", line)
                    
                    cur_sec = 0.0
                    if m_time:
                        h, m, s = m_time.groups()
                        cur_sec = int(h)*3600 + int(m)*60 + float(s)
                        
                    pct = min(100.0, (cur_sec / target_duration * 100)) if target_duration > 0 else 0
                    speed = m_speed.group(1) if m_speed else "1.0"
                    
                    if progress_callback:
                        progress_callback(pct, speed, cur_sec, target_duration)
                    
        proc.wait()
        
        if controller and controller.is_cancelled:
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except Exception:
                    pass
            raise RuntimeError("İşlem kullanıcı tarafından iptal edildi.")
            
        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg render hatası (Kod: {proc.returncode})")

# ==============================================================================
# 6. KULLANICI ARAYÜZÜ (TAM KİLİT SİSTEMLİ & ONAY POP-UP'LI TKINTER GUI)
# ==============================================================================

def launch_gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    import threading

    if HAS_TKDND:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()

    TelemetryTracker.track_event("app_launch")
    root.title(f"{APP_NAME} v{APP_VERSION} - Otomatik Video Sessizlik Kesici (GPU Destekli)")
    root.geometry("1040x790")
    root.minsize(940, 690)

    icon_path = get_binary_path("app_icon.ico")
    if os.path.exists(icon_path):
        try:
            root.iconbitmap(icon_path)
        except Exception:
            pass

    style = ttk.Style()
    style.theme_use("clam")

    output_dir_var = tk.StringVar(value="")
    status_text = tk.StringVar(value="Durum: Bekleniyor (Videoları sürükleyip bırakabilir veya Dosya Ekle ile seçebilirsiniz)")
    cur_percent_text = tk.StringVar(value="% 0.0")
    total_progress_text = tk.StringVar(value="Toplam İlerleme: 0 / 0")
    encoder_choice = tk.StringVar(value="auto")
    auto_open_folder = tk.BooleanVar(value=True)
    
    threshold_var = tk.DoubleVar(value=-30.0)
    min_silence_var = tk.DoubleVar(value=0.5)
    padding_var = tk.DoubleVar(value=0.15)
    
    video_list: List[Dict[str, Any]] = []
    controller = ProcessController()
    is_batch_running = False
    
    sort_state = {"col": None, "reverse": False}
    scan_queue = queue.Queue()
    scan_worker_running = True

    main_frame = ttk.Frame(root, padding="12")
    main_frame.pack(fill=tk.BOTH, expand=True)

    # 1. Video Listesi Alanı
    list_header = " 1. Video Kuyruğu (USB'den veya diskten sürükleyip bırakın | Başlıklara tıklayarak sıralayın) " if HAS_TKDND else " 1. Video Kuyruğu (Sütun başlıklarına tıklayarak sıralayın) "
    list_group = ttk.LabelFrame(main_frame, text=list_header, padding="8")
    list_group.pack(fill=tk.BOTH, expand=True, pady=4)

    btn_bar = ttk.Frame(list_group)
    btn_bar.pack(fill=tk.X, pady=(0, 6))

    tree_frame = ttk.Frame(list_group)
    tree_frame.pack(fill=tk.BOTH, expand=True)

    columns = ("filename", "duration", "silence_pct", "clean_duration", "resolution", "status", "path")
    tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=8, selectmode="extended")
    
    col_titles = {
        "filename": "Dosya Adı",
        "duration": "Orijinal Süre",
        "silence_pct": "Sessizlik Oranı (%)",
        "clean_duration": "Temiz Süre",
        "resolution": "Çözünürlük",
        "status": "Durum",
        "path": "Tam Yol"
    }

    tree.column("filename", width=220, anchor=tk.W)
    tree.column("duration", width=85, anchor=tk.CENTER)
    tree.column("silence_pct", width=130, anchor=tk.CENTER)
    tree.column("clean_duration", width=85, anchor=tk.CENTER)
    tree.column("resolution", width=95, anchor=tk.CENTER)
    tree.column("status", width=110, anchor=tk.CENTER)
    tree.column("path", width=290, anchor=tk.W)

    tree_scroll_y = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=tree.yview)
    tree.configure(yscrollcommand=tree_scroll_y.set)

    tree_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
    tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    def sort_by_column(col):
        if is_batch_running:
            return
            
        if sort_state["col"] == col:
            sort_state["reverse"] = not sort_state["reverse"]
        else:
            sort_state["col"] = col
            sort_state["reverse"] = False
            
        reverse = sort_state["reverse"]
        
        def get_sort_key(v):
            if col == "filename":
                return v["filename"].lower()
            elif col == "duration":
                return v.get("duration", 0.0)
            elif col == "silence_pct":
                return v.get("silence_pct", 0.0)
            elif col == "clean_duration":
                return v.get("clean_duration", v.get("duration", 0.0))
            elif col == "resolution":
                return v.get("resolution", "")
            elif col == "status":
                return v.get("status", "")
            elif col == "path":
                return v["path"].lower()
            return 0

        video_list.sort(key=get_sort_key, reverse=reverse)
        
        for idx, v in enumerate(video_list):
            tree.move(v["item_id"], "", idx)
            
        arrow = "  ▼" if reverse else "  ▲"
        for c in columns:
            base_title = col_titles.get(c, c)
            if c == col:
                tree.heading(c, text=base_title + arrow)
            else:
                tree.heading(c, text=base_title)

    for c in columns:
        tree.heading(c, text=col_titles[c], command=lambda _col=c: sort_by_column(_col))

    def refresh_total_label():
        total = len(video_list)
        done = sum(1 for v in video_list if v["status"] == "Tamamlandı")
        total_progress_text.set(f"Toplam İlerleme: {done} / {total} Video Tamamlandı")

    def format_sec(seconds: float) -> str:
        return f"{int(seconds//60):02d}:{int(seconds%60):02d}"

    def background_scan_worker():
        """Arka plan sırayla ses tarayıcısı (USB okuma korumalı)."""
        while scan_worker_running:
            try:
                v_item = scan_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            try:
                if v_item.get("duration", 0) == 0:
                    info = get_media_info(v_item["path"])
                    v_item["duration"] = info["duration"]
                    v_item["resolution"] = f"{info['width']}x{info['height']}"
                    
                    def update_meta(item_id=v_item["item_id"], dur_s=format_sec(info["duration"]), res_s=v_item["resolution"]):
                        try:
                            tree.set(item_id, "duration", dur_s)
                            tree.set(item_id, "resolution", res_s)
                        except Exception:
                            pass
                    root.after(0, update_meta)

                detector = SilenceDetector(
                    db_threshold=threshold_var.get(),
                    min_duration=min_silence_var.get(),
                    padding=padding_var.get()
                )
                silences = detector.detect_silence_intervals(v_item["path"])
                speech = detector.compute_speech_intervals(v_item["duration"], silences)
                
                kept_dur = sum(e - s for s, e in speech)
                cut_dur = max(0.0, v_item["duration"] - kept_dur)
                pct = (cut_dur / v_item["duration"] * 100) if v_item["duration"] > 0 else 0.0
                
                v_item["speech_intervals"] = speech
                v_item["silence_pct"] = pct
                v_item["clean_duration"] = kept_dur
                v_item["cut_duration"] = cut_dur
                
                silence_str = f"% {pct:.1f} ({cut_dur:.1f}sn)" if pct > 0 else "% 0.0 (Yok)"
                clean_dur_str = format_sec(kept_dur)
                status_str = "Hazır" if pct > 0 else "Sessizlik Yok"
                v_item["status"] = status_str
                
                def update_ui(item_id=v_item["item_id"], s_str=silence_str, c_str=clean_dur_str, st_str=status_str):
                    try:
                        tree.set(item_id, "silence_pct", s_str)
                        tree.set(item_id, "clean_duration", c_str)
                        tree.set(item_id, "status", st_str)
                    except Exception:
                        pass
                    
                root.after(0, update_ui)
            except Exception as e:
                def update_err(item_id=v_item["item_id"]):
                    try:
                        tree.set(item_id, "status", "Okuma Hatası")
                    except Exception:
                        pass
                root.after(0, update_err)
            finally:
                scan_queue.task_done()

    threading.Thread(target=background_scan_worker, daemon=True).start()

    def rescan_all_videos():
        if is_batch_running:
            return
        log("Tüm videolar yeni ayarlarla sırayla taranıyor...")
        for v in video_list:
            tree.set(v["item_id"], "silence_pct", "Taranıyor...")
            tree.set(v["item_id"], "clean_duration", "...")
            tree.set(v["item_id"], "status", "Taranıyor...")
            scan_queue.put(v)

    def add_paths_to_queue(paths: List[str]):
        """Gelen dosya veya klasör yollarını kuyruğa ekler."""
        if is_batch_running:
            messagebox.showwarning("İşlem Sürüyor", "Render işlemi devam ederken listeye yeni video eklenemez.")
            return

        exts = (".mp4", ".mov", ".mkv", ".avi", ".m4v")
        collected_files = []
        
        for p in paths:
            if isinstance(p, bytes):
                try:
                    p = p.decode('utf-8', errors='ignore')
                except Exception:
                    p = os.fsdecode(p)
            p = str(p).strip().strip('"').strip("'")
            p = os.path.abspath(p)
            
            if os.path.isfile(p) and p.lower().endswith(exts):
                collected_files.append(p)
            elif os.path.isdir(p):
                for root_dir, _, fns in os.walk(p):
                    for fn in fns:
                        if fn.lower().endswith(exts):
                            collected_files.append(os.path.abspath(os.path.join(root_dir, fn)))

        added_count = 0
        for norm_f in collected_files:
            if any(v["path"] == norm_f for v in video_list):
                continue
            
            fn = os.path.basename(norm_f)
            item_id = tree.insert("", tk.END, values=(fn, "Okunuyor...", "Taranıyor...", "...", "...", "Sırada...", norm_f))
            v_entry = {
                "path": norm_f,
                "filename": fn,
                "duration": 0.0,
                "resolution": "",
                "status": "Sırada...",
                "silence_pct": 0.0,
                "clean_duration": 0.0,
                "speech_intervals": None,
                "item_id": item_id
            }
            video_list.append(v_entry)
            added_count += 1
            scan_queue.put(v_entry)
                
        refresh_total_label()
        status_text.set(f"Durum: {len(video_list)} video listede.")
        if added_count > 0:
            log(f"{added_count} video listeye eklendi, arka planda sırayla taranıyor.")

    # TkinterDnD2 Resmi Sürükle-Bırak Olayı
    if HAS_TKDND:
        def on_tkdnd_drop(event):
            try:
                raw_data = event.data
                matches = re.findall(r'\{([^}]+)\}|(\S+)', raw_data)
                parsed_paths = []
                for m in matches:
                    p = m[0] if m[0] else m[1]
                    if p:
                        parsed_paths.append(p)
                add_paths_to_queue(parsed_paths)
            except Exception as e:
                log(f"Sürükle-Bırak Hatası: {e}")

        root.drop_target_register(DND_FILES)
        root.dnd_bind('<<Drop>>', on_tkdnd_drop)
        tree.drop_target_register(DND_FILES)
        tree.dnd_bind('<<Drop>>', on_tkdnd_drop)

    def add_files_dialog():
        files = filedialog.askopenfilenames(filetypes=[("Video Dosyaları", "*.mp4 *.mov *.mkv *.avi *.m4v")])
        if files:
            add_paths_to_queue(list(files))

    def add_folder_dialog():
        folder = filedialog.askdirectory()
        if folder:
            add_paths_to_queue([folder])

    def remove_selected():
        selected = tree.selection()
        if not selected:
            return
        for s in selected:
            for v in list(video_list):
                if v["item_id"] == s:
                    video_list.remove(v)
            tree.delete(s)
        refresh_total_label()

    def clear_all():
        if is_batch_running:
            messagebox.showwarning("Uyarı", "İşlem devam ederken liste temizlenemez.")
            return
        for item in tree.get_children():
            tree.delete(item)
        video_list.clear()
        refresh_total_label()
        status_text.set("Durum: Liste temizlendi.")

    btn_add_files = ttk.Button(btn_bar, text="➕ Dosya Ekle...", command=add_files_dialog)
    btn_add_folder = ttk.Button(btn_bar, text="📁 Klasör Ekle (USB/Disk)...", command=add_folder_dialog)
    btn_remove_sel = ttk.Button(btn_bar, text="➖ Seçileni Kaldır", command=remove_selected)
    btn_clear_all = ttk.Button(btn_bar, text="🗑️ Listeyi Temizle", command=clear_all)
    btn_rescan = ttk.Button(btn_bar, text="🔄 Değerleri Yeniden Tara", command=rescan_all_videos)

    btn_add_files.pack(side=tk.LEFT, padx=3)
    btn_add_folder.pack(side=tk.LEFT, padx=3)
    btn_remove_sel.pack(side=tk.LEFT, padx=3)
    btn_clear_all.pack(side=tk.LEFT, padx=3)
    btn_rescan.pack(side=tk.LEFT, padx=8)

    # 2. Çıktı Klasörü Alanı
    out_group = ttk.LabelFrame(main_frame, text=" 2. Çıktı Klasörü Ayarları (USB'den okurken SSD/Masaüstüne kaydetmek daha hızlıdır) ", padding="8")
    out_group.pack(fill=tk.X, pady=4)

    ttk.Label(out_group, text="Kaydedilecek Klasör:").grid(row=0, column=0, sticky=tk.W, pady=2)
    entry_out_dir = ttk.Entry(out_group, textvariable=output_dir_var, width=54)
    entry_out_dir.grid(row=0, column=1, padx=5, pady=2)
    
    def browse_out_dir():
        d = filedialog.askdirectory()
        if d:
            output_dir_var.set(d)

    def set_desktop_out():
        desktop = os.path.join(os.path.expanduser("~"), "Desktop", "EZVideo_Cut_Ciktilar")
        os.makedirs(desktop, exist_ok=True)
        output_dir_var.set(desktop)
            
    btn_browse_out = ttk.Button(out_group, text="Gözat...", command=browse_out_dir)
    btn_desktop_out = ttk.Button(out_group, text="💻 Masaüstüne Kaydet", command=set_desktop_out)
    btn_browse_out.grid(row=0, column=2, padx=2, pady=2)
    btn_desktop_out.grid(row=0, column=3, padx=2, pady=2)
    
    check_open = ttk.Checkbutton(out_group, text="📁 İşlem bitince çıktı klasörünü otomatik olarak aç", variable=auto_open_folder)
    check_open.grid(row=1, column=1, sticky=tk.W, pady=2)

    # 3. Parametre Ayarları & GPU Seçimi
    param_group = ttk.LabelFrame(main_frame, text=" 3. Kesim Parametreleri & Donanım Hızlandırma ", padding="8")
    param_group.pack(fill=tk.X, pady=4)

    ttk.Label(param_group, text="Sessizlik Eşiği (dB):").grid(row=0, column=0, sticky=tk.W, pady=2)
    thresh_scale = ttk.Scale(param_group, from_=-60.0, to=-10.0, variable=threshold_var, orient=tk.HORIZONTAL)
    thresh_scale.grid(row=0, column=1, sticky=tk.EW, padx=5)
    thresh_lbl = ttk.Label(param_group, text=f"{threshold_var.get():.1f} dB")
    thresh_lbl.grid(row=0, column=2, sticky=tk.W)
    thresh_scale.configure(command=lambda val: thresh_lbl.configure(text=f"{float(val):.1f} dB"))

    ttk.Label(param_group, text="Min. Sessizlik (sn):").grid(row=1, column=0, sticky=tk.W, pady=2)
    min_scale = ttk.Scale(param_group, from_=0.1, to=2.0, variable=min_silence_var, orient=tk.HORIZONTAL)
    min_scale.grid(row=1, column=1, sticky=tk.EW, padx=5)
    min_lbl = ttk.Label(param_group, text=f"{min_silence_var.get():.2f} sn")
    min_lbl.grid(row=1, column=2, sticky=tk.W)
    min_scale.configure(command=lambda val: min_lbl.configure(text=f"{float(val):.2f} sn"))

    ttk.Label(param_group, text="Güvenlik Payı (sn):").grid(row=2, column=0, sticky=tk.W, pady=2)
    pad_scale = ttk.Scale(param_group, from_=0.0, to=0.5, variable=padding_var, orient=tk.HORIZONTAL)
    pad_scale.grid(row=2, column=1, sticky=tk.EW, padx=5)
    pad_lbl = ttk.Label(param_group, text=f"{padding_var.get():.2f} sn")
    pad_lbl.grid(row=2, column=2, sticky=tk.W)
    pad_scale.configure(command=lambda val: pad_lbl.configure(text=f"{float(val):.2f} sn"))

    ttk.Label(param_group, text="Render Donanımı:").grid(row=3, column=0, sticky=tk.W, pady=2)
    enc_combo = ttk.Combobox(param_group, textvariable=encoder_choice, state="readonly", width=36)
    enc_combo["values"] = (
        "Otomatik (En Hızlı Donanım / GPU)",
        "NVIDIA NVENC (GeForce / RTX GPU)",
        "Intel QuickSync (Intel GPU / iGPU)",
        "AMD AMF (Radeon GPU)",
        "CPU Ultrafast (İşlemci Çok Çekirdek)"
    )
    enc_combo.current(0)
    enc_combo.grid(row=3, column=1, sticky=tk.W, padx=5, pady=2)

    # 4. Aksiyon Butonları & Kontrol
    act_group = ttk.Frame(main_frame, padding="4")
    act_group.pack(fill=tk.X, pady=3)

    btn_start_batch = ttk.Button(act_group, text="⚡ TOPLU RENDER BAŞLAT")
    btn_pause = ttk.Button(act_group, text="⏸ Duraklat", state=tk.DISABLED)
    btn_cancel = ttk.Button(act_group, text="⏹ İptal Et", state=tk.DISABLED)

    btn_start_batch.pack(side=tk.LEFT, padx=3)
    btn_pause.pack(side=tk.LEFT, padx=6)
    btn_cancel.pack(side=tk.LEFT, padx=3)

    lbl_total_prog = ttk.Label(act_group, textvariable=total_progress_text, font=("Segoe UI", 9, "bold"), foreground="#007acc")
    lbl_total_prog.pack(side=tk.RIGHT, padx=5)

    # 5. İlerleme Çubuğu & Yüzde
    prog_frame = ttk.Frame(main_frame, padding="4")
    prog_frame.pack(fill=tk.X, pady=2)

    progress_bar = ttk.Progressbar(prog_frame, orient=tk.HORIZONTAL, mode='determinate')
    progress_bar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))

    lbl_percent = ttk.Label(prog_frame, textvariable=cur_percent_text, font=("Segoe UI", 11, "bold"), foreground="#007acc", width=8)
    lbl_percent.pack(side=tk.RIGHT)

    lbl_status = ttk.Label(main_frame, textvariable=status_text, font=("Segoe UI", 9, "italic"), foreground="#444")
    lbl_status.pack(anchor=tk.W, pady=2)

    log_box = tk.Text(main_frame, height=8, bg="#1e1e1e", fg="#e0e0e0", font=("Consolas", 9))
    log_box.pack(fill=tk.BOTH, expand=True, pady=4)

    def log(msg):
        log_box.insert(tk.END, msg + "\n")
        log_box.see(tk.END)

    def get_selected_encoder_key():
        val = enc_combo.current()
        if val == 1:
            return "nvenc"
        elif val == 2:
            return "qsv"
        elif val == 3:
            return "amf"
        elif val == 4:
            return "cpu"
        return "auto"

    def set_ui_running(running: bool):
        """Render başladığında kilitlenecek ve açılacak tüm arayüz elemanlarını yönetir."""
        nonlocal is_batch_running
        is_batch_running = running
        
        lock_state = tk.DISABLED if running else tk.NORMAL
        
        # 1. Üstteki butonlar kilitlenir
        btn_add_files.configure(state=lock_state)
        btn_add_folder.configure(state=lock_state)
        btn_remove_sel.configure(state=lock_state)
        btn_clear_all.configure(state=lock_state)
        btn_rescan.configure(state=lock_state)
        
        # 2. Çıktı klasörü alanı kilitlenir
        entry_out_dir.configure(state=lock_state)
        btn_browse_out.configure(state=lock_state)
        btn_desktop_out.configure(state=lock_state)
        check_open.configure(state=lock_state)
        
        # 3. Kesim parametreleri ve donanım seçimi kilitlenir
        thresh_scale.configure(state=lock_state)
        min_scale.configure(state=lock_state)
        pad_scale.configure(state=lock_state)
        enc_combo.configure(state=lock_state)
        
        # 4. Başlat butonu kilitlenir, sadece Duraklat ve İptal Et aktif kalır
        btn_start_batch.configure(state=tk.DISABLED if running else tk.NORMAL)
        btn_pause.configure(state=tk.NORMAL if running else tk.DISABLED, text="⏸ Duraklat")
        btn_cancel.configure(state=tk.NORMAL if running else tk.DISABLED)

    def toggle_pause():
        if not controller.is_paused:
            if controller.suspend():
                btn_pause.configure(text="▶ Devam Ettir")
                status_text.set("⏸ İşlem Duraklatıldı.")
                log(">>> İşlem Duraklatıldı <<<")
        else:
            if controller.resume():
                btn_pause.configure(text="⏸ Duraklat")
                status_text.set("▶ İşlem Devam Ediyor...")
                log(">>> İşlem Devam Ediyor <<<")

    def run_cancel():
        """İptal et butonuna basıldığında onay pop-up'ı gösterir."""
        was_already_paused = controller.is_paused
        
        # Kullanıcı pop-up'a bakarken işlemciyi yormamak için geçici duraklat
        if not was_already_paused:
            controller.suspend()

        answer = messagebox.askyesno(
            "İşlemi İptal Et",
            "Devam eden toplu render işlemini iptal etmek istediğinize emin misiniz?\n\nYarım kalan geçerli dosya temizlenecektir.",
            icon="warning"
        )
        
        if answer:
            log(">>> Toplu render işlemi kullanıcı tarafından iptal ediliyor... <<<")
            status_text.set("Durum: İşlem iptal ediliyor...")
            controller.cancel()
        else:
            # Kullanıcı 'Hayır' dediyse işleme kaldığı yerden devam et
            if not was_already_paused:
                controller.resume()

    btn_pause.configure(command=toggle_pause)
    btn_cancel.configure(command=run_cancel)

    def start_batch_worker():
        if not video_list:
            messagebox.showwarning("Uyarı", "Lütfen önce listeye en az bir video ekleyin.")
            return

        def worker():
            set_ui_running(True)
            custom_out_dir = output_dir_var.get().strip()
            if custom_out_dir and not os.path.exists(custom_out_dir):
                os.makedirs(custom_out_dir, exist_ok=True)

            detector = SilenceDetector(
                db_threshold=threshold_var.get(),
                min_duration=min_silence_var.get(),
                padding=padding_var.get()
            )
            enc_mode = get_selected_encoder_key()
            
            total_videos = len(video_list)
            last_successful_output = None
            
            log(f"==================================================")
            log(f"Toplu Render Başlatıldı: Toplam {total_videos} video işlenecek.")
            log(f"==================================================")

            for idx, v_item in enumerate(video_list):
                if controller.is_cancelled:
                    break
                    
                v_path = v_item["path"]
                v_name = v_item["filename"]
                item_id = v_item["item_id"]

                if custom_out_dir:
                    base_name, ext = os.path.splitext(v_name)
                    out_path = os.path.join(custom_out_dir, f"{base_name}_temiz{ext}")
                else:
                    base_path, ext = os.path.splitext(v_path)
                    out_path = f"{base_path}_temiz{ext}"

                progress_bar['value'] = 0
                cur_percent_text.set("% 0.0")

                try:
                    speech = v_item.get("speech_intervals")
                    if not speech or v_item.get("duration", 0) == 0:
                        tree.set(item_id, "status", "Taranıyor...")
                        status_text.set(f"[{idx+1}/{total_videos}] Taranıyor: {v_name}")
                        info = get_media_info(v_path)
                        v_item["duration"] = info["duration"]
                        silences = detector.detect_silence_intervals(v_path, controller=controller)
                        if controller.is_cancelled:
                            tree.set(item_id, "status", "İptal Edildi")
                            break
                        speech = detector.compute_speech_intervals(v_item["duration"], silences)
                        v_item["speech_intervals"] = speech

                    if not speech:
                        tree.set(item_id, "status", "Hata: Konuşma Yok")
                        log(f"Hata: {v_name} için konuşma aralığı bulunamadı.")
                        continue

                    # Render Alma
                    tree.set(item_id, "status", "Render Ediliyor")
                    log(f"\n[{idx+1}/{total_videos}] Render Başladı -> {os.path.basename(out_path)}")

                    def on_prog(pct, speed, cur_sec, total_sec):
                        progress_bar['value'] = pct
                        cur_percent_text.set(f"% {pct:.1f}")
                        eta_sec = max(0, (total_sec - cur_sec) / float(speed)) if float(speed) > 0 else 0
                        status_text.set(f"[{idx+1}/{total_videos}] {v_name} | %{pct:.1f} | Hız: {speed}x | Kalan: {eta_sec:.0f}sn")
                        root.update_idletasks()

                    VideoRenderer.render_fast(
                        video_path=v_path,
                        intervals=speech,
                        output_path=out_path,
                        encoder_mode=enc_mode,
                        controller=controller,
                        log_callback=log,
                        progress_callback=on_prog
                    )

                    if controller.is_cancelled:
                        tree.set(item_id, "status", "İptal Edildi")
                        break

                    tree.set(item_id, "status", "Tamamlandı")
                    v_item["status"] = "Tamamlandı"
                    last_successful_output = out_path
                    progress_bar['value'] = 100
                    cur_percent_text.set("% 100.0")
                    log(f"✔ Başarıyla Tamamlandı: {os.path.basename(out_path)}")
                    refresh_total_label()

                except Exception as e:
                    if controller.is_cancelled:
                        tree.set(item_id, "status", "İptal Edildi")
                        break
                    else:
                        tree.set(item_id, "status", "Hata")
                        log(f"Render Hatası ({v_name}): {e}")

            set_ui_running(False)
            if controller.is_cancelled:
                status_text.set("Durum: Toplu işlem kullanıcı tarafından iptal edildi.")
                log("\n>>> TOPLU RENDER İPTAL EDİLDİ <<<")
                messagebox.showwarning("İptal Edildi", "Toplu render işlemi başarıyla iptal edildi.")
            else:
                status_text.set("Durum: Tüm videolar başarıyla işlendi!")
                log("\n==================================================")
                log(">>> TÜM VİDEOLARIN RENDER İŞLEMİ TAMAMLANDI <<<")
                log("==================================================")
                
                if auto_open_folder.get() and last_successful_output:
                    target_folder = custom_out_dir if custom_out_dir else last_successful_output
                    open_output_folder(target_folder)
                    
                messagebox.showinfo("Başarılı", f"Toplu render tamamlandı!\nTüm temizlenmiş videolar kaydedildi.")

        threading.Thread(target=worker, daemon=True).start()

    btn_start_batch.configure(command=start_batch_worker)

    root.mainloop()

# ==============================================================================
# 7. KOMUT SATIRI (CLI BATCH)
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(description="EZVideo Cut: Toplu video sessizlik kesici ve kurgu aracı.")
    parser.add_argument("--gui", action="store_true", help="Grafik arayüzü başlatır.")
    parser.add_argument("-i", "--inputs", nargs="+", help="Giriş video dosyaları (birden fazla seçilebilir)")
    parser.add_argument("-o", "--output-dir", type=str, default="", help="Çıktı klasörü")
    parser.add_argument("--encoder", type=str, default="auto", choices=["auto", "nvenc", "qsv", "amf", "cpu"], help="Enkoder seçimi")
    parser.add_argument("--threshold", type=float, default=-30.0, help="Sessizlik eşik değeri dB (varsayılan: -30)")
    parser.add_argument("--min-silence", type=float, default=0.5, help="Minimum sessizlik süresi saniye (varsayılan: 0.5)")
    parser.add_argument("--padding", type=float, default=0.15, help="Konuşma güvenlik payı saniye (varsayılan: 0.15)")
    parser.add_argument("--no-open", action="store_true", help="İşlem bitince klasörü açma")
    
    args = parser.parse_args()

    if args.gui or (len(sys.argv) == 1):
        if not check_ffmpeg():
            print("Hata: FFmpeg bulunamadı. Lütfen sisteminize FFmpeg kurun.")
            sys.exit(1)
        launch_gui()
        return

    if not args.inputs:
        parser.print_help()
        sys.exit(1)

    if not check_ffmpeg():
        print("Hata: FFmpeg bulunamadı. Lütfen sisteminize FFmpeg kurun.")
        sys.exit(1)

    detector = SilenceDetector(db_threshold=args.threshold, min_duration=args.min_silence, padding=args.padding)
    out_dir = args.output_dir
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    print(f"Toplu işlem başlatılıyor: Toplam {len(args.inputs)} video.")
    last_file = None
    for idx, v_path in enumerate(args.inputs):
        info = get_media_info(v_path)
        base_name, ext = os.path.splitext(os.path.basename(v_path))
        if out_dir:
            out_file = os.path.join(out_dir, f"{base_name}_temiz{ext}")
        else:
            base_p, _ = os.path.splitext(v_path)
            out_file = f"{base_p}_temiz{ext}"

        print(f"\n[{idx+1}/{len(args.inputs)}] {info['filename']} taranıyor...")
        silences = detector.detect_silence_intervals(v_path)
        speech = detector.compute_speech_intervals(info['duration'], silences)
        
        kept = sum(e - s for s, e in speech)
        cut = info['duration'] - kept
        pct = (cut / info['duration'] * 100) if info['duration'] > 0 else 0
        print(f"Sessizlik Oranı: %{pct:.1f} ({cut:.1f}sn atılacak) -> Yeni Süre: {kept:.1f}sn")
        
        VideoRenderer.render_fast(v_path, speech, out_file, encoder_mode=args.encoder)
        last_file = out_file
        print(f"Tamamlandı -> {out_file}")

    if not args.no_open and last_file:
        open_output_folder(out_dir if out_dir else last_file)

if __name__ == "__main__":
    main()
