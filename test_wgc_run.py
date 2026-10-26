import ctypes
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32")
kernel32 = ctypes.WinDLL("kernel32")

# Cria uma janela Win32 de teste
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
def def_wnd_proc(hwnd, msg, wp, lp):
    return user32.DefWindowProcW(hwnd, msg, wp, lp)
proc = WNDPROC(def_wnd_proc)

class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT), ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON), ("hCursor", wintypes.HICON),
        ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON)
    ]

cls = WNDCLASSEXW()
cls.cbSize = ctypes.sizeof(cls)
cls.lpfnWndProc = proc
cls.lpszClassName = "LuceWGCTestWindow"
cls.hInstance = kernel32.GetModuleHandleW(None)
atom = user32.RegisterClassExW(ctypes.byref(cls))
print("RegisterClassExW:", atom)

hwnd = user32.CreateWindowExW(
    0, cls.lpszClassName, "Luce WGC Test Window",
    0x10CF0000, # WS_VISIBLE | WS_OVERLAPPEDWINDOW
    100, 100, 800, 600,
    None, None, cls.hInstance, None
)
print("CreateWindowExW hwnd:", hwnd)
assert hwnd, "Janela falhou"

# Processa mensagens para renderizar a janela
msg = wintypes.MSG()
for _ in range(10):
    if user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
    time.sleep(0.01)

# Carrega DLL e testa WGC
lib = ctypes.CDLL("./luce_wgc.dll")
lib.luce_wgc_is_supported.restype = ctypes.c_int
lib.luce_wgc_create.argtypes = [wintypes.HWND, ctypes.c_int]
lib.luce_wgc_create.restype = ctypes.c_void_p
lib.luce_wgc_grab_frame.argtypes = [
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_uint32),
    ctypes.POINTER(ctypes.c_uint32),
    ctypes.c_char_p,
    ctypes.c_uint32,
    ctypes.c_int
]
lib.luce_wgc_grab_frame.restype = ctypes.c_int
lib.luce_wgc_destroy.argtypes = [ctypes.c_void_p]

print("is_supported:", lib.luce_wgc_is_supported())
handle = lib.luce_wgc_create(hwnd, 1)
print("handle:", handle)
assert handle, "luce_wgc_create falhou"

try:
    buf_size = 1920 * 1080 * 4
    buf = bytearray(buf_size)
    c_buf = (ctypes.c_char * buf_size).from_buffer(buf)
    out_w = ctypes.c_uint32(0)
    out_h = ctypes.c_uint32(0)

    frames = 0
    t0 = time.time()
    for i in range(30):
        # processa mensagens
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        res = lib.luce_wgc_grab_frame(
            handle,
            ctypes.byref(out_w),
            ctypes.byref(out_h),
            c_buf,
            buf_size,
            50
        )
        if res == 1:
            frames += 1
            if frames == 1:
                print(f"Primeiro quadro capturado: {out_w.value}x{out_h.value}, byte sample: {list(buf[:8])}")
        time.sleep(0.016)

    dt = time.time() - t0
    print(f"Total quadros capturados: {frames} em {dt:.2f}s")
    assert frames > 0, "Nenhum quadro capturado!"
finally:
    lib.luce_wgc_destroy(handle)
    user32.DestroyWindow(hwnd)

print("SUCESSO: WGC funcionou perfeitamente!")
