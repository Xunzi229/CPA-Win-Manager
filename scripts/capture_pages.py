import ctypes
from ctypes import windll, wintypes, byref
from pathlib import Path
import sys
import time
import tkinter as tk
from PIL import Image

try:
    windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    windll.user32.SetProcessDPIAware()

class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ('biSize', wintypes.DWORD),
        ('biWidth', wintypes.LONG),
        ('biHeight', wintypes.LONG),
        ('biPlanes', wintypes.WORD),
        ('biBitCount', wintypes.WORD),
        ('biCompression', wintypes.DWORD),
        ('biSizeImage', wintypes.DWORD),
        ('biXPelsPerMeter', wintypes.LONG),
        ('biYPelsPerMeter', wintypes.LONG),
        ('biClrUsed', wintypes.DWORD),
        ('biClrImportant', wintypes.DWORD)
    ]

class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ('bmiHeader', BITMAPINFOHEADER),
        ('bmiColors', wintypes.DWORD * 3)
    ]

def capture_window(hwnd):
    rect = wintypes.RECT()
    windll.user32.GetWindowRect(hwnd, byref(rect))
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return None
    
    hwnd_dc = windll.user32.GetWindowDC(hwnd)
    mfc_dc = windll.gdi32.CreateCompatibleDC(hwnd_dc)
    save_bitmap = windll.gdi32.CreateCompatibleBitmap(hwnd_dc, width, height)
    windll.gdi32.SelectObject(mfc_dc, save_bitmap)
    
    result = windll.user32.PrintWindow(hwnd, mfc_dc, 2)
    if not result:
        windll.user32.PrintWindow(hwnd, mfc_dc, 0)
        
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = width
    bmi.bmiHeader.biHeight = -height
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0
    
    buffer = ctypes.create_string_buffer(width * height * 4)
    windll.gdi32.GetDIBits(hwnd_dc, save_bitmap, 0, height, buffer, byref(bmi), 0)
    
    windll.gdi32.DeleteObject(save_bitmap)
    windll.gdi32.DeleteDC(mfc_dc)
    windll.user32.ReleaseDC(hwnd, hwnd_dc)
    
    img = Image.frombuffer('RGBA', (width, height), buffer, 'raw', 'BGRA', 0, 1)
    return img.convert('RGB')

def get_toplevel_hwnd(widget):
    return windll.user32.GetAncestor(widget.winfo_id(), 2)

def main():
    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root))
    
    img_dir = repo_root / "img"
    img_dir.mkdir(exist_ok=True)
    
    from cpa_manager.app import App
    
    # 禁用自动网络检查，保持纯净界面状态
    App.manager_check_due = lambda *a, **k: False
    App.auto_check_manager_update = lambda *a, **k: None
    App.check_manager_update = lambda *a, **k: None
    
    print("正在启动开源软件管理器...")
    app = App()
    app.manager_busy = False
    
    # 调整窗口居中与大小
    app.window.geometry("980x820")
    app.window.update_idletasks()
    app.window.deiconify()
    app.window.lift()
    app.window.focus_force()
    
    main_hwnd = get_toplevel_hwnd(app.window)
    notebook = app.pages[0].frame.master
    
    # 1. 截取所有主 Tab 页面
    tab_names = [notebook.tab(i, "text").strip() for i in range(len(app.pages))]
    print(f"检测到主标签页: {tab_names}")
    
    for idx, (page, name) in enumerate(zip(app.pages, tab_names), 1):
        notebook.select(page.frame)
        app.window.update_idletasks()
        app.window.update()
        time.sleep(0.4)
        
        img = capture_window(main_hwnd)
        if img:
            save_path = img_dir / f"{idx:02d}-{name}.png"
            img.save(save_path)
            print(f"已保存页面截图: {save_path.name} ({img.size[0]}x{img.size[1]})")

    # 2. 截取“设置”弹窗
    print("正在打开设置窗口...")
    app.open_proxy_settings()
    app.window.update_idletasks()
    app.window.update()
    time.sleep(0.4)
    if app.proxy_dialog and app.proxy_dialog.winfo_exists():
        app.proxy_dialog.update_idletasks()
        app.proxy_dialog.update()
        dialog_hwnd = get_toplevel_hwnd(app.proxy_dialog)
        img = capture_window(dialog_hwnd)
        if img:
            save_path = img_dir / "05-设置.png"
            img.save(save_path)
            print(f"已保存弹窗截图: {save_path.name} ({img.size[0]}x{img.size[1]})")
        app.proxy_dialog.destroy()
        app.proxy_dialog = None
        app.window.update()
        time.sleep(0.2)

    # 3. 截取“软件库”弹窗
    print("正在打开软件库窗口...")
    notebook.select(app.installer_page.frame)
    app.window.update()
    app.installer_page.open_software_library()
    app.window.update_idletasks()
    app.window.update()
    time.sleep(0.4)
    
    lib = getattr(app.installer_page, "software_library", None)
    if lib and lib.window.winfo_exists():
        lib.window.update_idletasks()
        lib.window.update()
        lib_hwnd = get_toplevel_hwnd(lib.window)
        img = capture_window(lib_hwnd)
        if img:
            save_path = img_dir / "06-软件库.png"
            img.save(save_path)
            print(f"已保存弹窗截图: {save_path.name} ({img.size[0]}x{img.size[1]})")
        lib.window.destroy()
        app.installer_page.software_library = None
        app.window.update()
        time.sleep(0.2)

    # 4. 截取“关联已安装软件”弹窗
    print("正在打开关联已安装软件窗口...")
    try:
        if app.installer_profiles:
            app.installer_page.table.selection_set(app.installer_profiles[0]["id"])
            app.installer_page.show_selection()
            # 确保有扫描结果可供展示
            if not app.installer_page.installed_records:
                app.installer_page.installed_records = [
                    {"id": "clash-verge", "name": "Clash Verge", "version": "2.5.8", "directory": r"C:\Program Files\Clash Verge"},
                    {"id": "clash-party", "name": "Clash Party", "version": "2.0.3", "directory": r"C:\Program Files\Clash Party"},
                    {"id": "powertoys", "name": "PowerToys (Preview)", "version": "0.101.2362.0", "directory": r"C:\Program Files\PowerToys"},
                ]
            app.installer_page.local_scanned = True
            
            # 记录当前已存在的 toplevels
            existing_toplevels = set(w for w in app.window.winfo_children() if isinstance(w, tk.Toplevel))
            app.installer_page.associate_installed()
            app.window.update_idletasks()
            app.window.update()
            time.sleep(0.4)
            
            new_toplevels = [w for w in app.window.winfo_children() if isinstance(w, tk.Toplevel) and w not in existing_toplevels]
            for dialog in new_toplevels:
                if dialog.winfo_exists():
                    dialog.update_idletasks()
                    dialog.update()
                    dialog_hwnd = get_toplevel_hwnd(dialog)
                    img = capture_window(dialog_hwnd)
                    if img:
                        save_path = img_dir / "07-关联已安装软件.png"
                        img.save(save_path)
                        print(f"已保存弹窗截图: {save_path.name} ({img.size[0]}x{img.size[1]})")
                    dialog.destroy()
                    break
    except Exception as e:
        print(f"关联已安装软件窗口处理异常: {e}")

    app.window.destroy()
    print("所有页面截图已全部完成！")

if __name__ == "__main__":
    main()
