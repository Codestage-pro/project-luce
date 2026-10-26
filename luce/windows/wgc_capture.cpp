#include <windows.h>
#include <winstring.h>
#include <roapi.h>
#include <d3d11.h>
#include <dxgi1_2.h>
#include <windows.graphics.capture.h>
#include <windows.graphics.capture.interop.h>
#include <windows.graphics.directx.direct3d11.h>
#include <windows.graphics.directx.direct3d11.interop.h>
#include <wrl/client.h>
#include <wrl/event.h>
#include <cstdint>
#include <cstring>

using namespace Microsoft::WRL;
using namespace ABI::Windows::Foundation;
using namespace ABI::Windows::Graphics;
using namespace ABI::Windows::Graphics::Capture;
using namespace ABI::Windows::Graphics::DirectX;
using namespace ABI::Windows::Graphics::DirectX::Direct3D11;
using namespace Windows::Graphics::DirectX::Direct3D11;

struct LuceWGC {
    HWND hwnd = nullptr;
    ComPtr<ID3D11Device> device;
    ComPtr<ID3D11DeviceContext> context;
    ComPtr<IDirect3DDevice> winrt_device;
    ComPtr<IGraphicsCaptureItem> item;
    ComPtr<IDirect3D11CaptureFramePool> pool;
    ComPtr<IGraphicsCaptureSession> session;
    ComPtr<ID3D11Texture2D> staging;
    uint32_t staging_width = 0;
    uint32_t staging_height = 0;
    uint32_t width = 0;
    uint32_t height = 0;
    HANDLE frame_event = nullptr;
    EventRegistrationToken frame_token{};
};

template<typename T>
static HRESULT GetActivationFactory(const wchar_t* class_name, ComPtr<T>& factory) {
    HSTRING hstr = nullptr;
    HRESULT hr = WindowsCreateString(class_name, (UINT32)wcslen(class_name), &hstr);
    if (FAILED(hr)) return hr;
    hr = RoGetActivationFactory(hstr, IID_PPV_ARGS(&factory));
    WindowsDeleteString(hstr);
    return hr;
}

static bool CreateStagingTexture(LuceWGC* wgc, uint32_t width, uint32_t height) {
    D3D11_TEXTURE2D_DESC desc = {};
    desc.Width = width;
    desc.Height = height;
    desc.MipLevels = 1;
    desc.ArraySize = 1;
    desc.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
    desc.SampleDesc.Count = 1;
    desc.Usage = D3D11_USAGE_STAGING;
    desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;

    wgc->staging.Reset();
    HRESULT hr = wgc->device->CreateTexture2D(&desc, nullptr, &wgc->staging);
    if (FAILED(hr)) return false;
    wgc->staging_width = width;
    wgc->staging_height = height;
    return true;
}

extern "C" {

__declspec(dllexport) int luce_wgc_is_supported(void) {
    RoInitialize(RO_INIT_MULTITHREADED);
    ComPtr<IGraphicsCaptureItemInterop> interop;
    HRESULT hr = GetActivationFactory(L"Windows.Graphics.Capture.GraphicsCaptureItem", interop);
    return SUCCEEDED(hr) && interop != nullptr ? 1 : 0;
}

__declspec(dllexport) LuceWGC* luce_wgc_create(HWND hwnd, int capture_cursor) {
    if (!hwnd || !IsWindow(hwnd)) return nullptr;

    RoInitialize(RO_INIT_MULTITHREADED);

    LuceWGC* wgc = new LuceWGC();
    wgc->hwnd = hwnd;

    D3D_FEATURE_LEVEL feature_levels[] = {
        D3D_FEATURE_LEVEL_11_1,
        D3D_FEATURE_LEVEL_11_0,
        D3D_FEATURE_LEVEL_10_1,
        D3D_FEATURE_LEVEL_10_0,
    };
    D3D_FEATURE_LEVEL level;
    UINT flags = D3D11_CREATE_DEVICE_BGRA_SUPPORT;
    HRESULT hr = D3D11CreateDevice(
        nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr,
        flags, feature_levels, ARRAYSIZE(feature_levels),
        D3D11_SDK_VERSION, &wgc->device, &level, &wgc->context
    );
    if (FAILED(hr)) {
        // Tenta WARP caso o driver de GPU falhe
        hr = D3D11CreateDevice(
            nullptr, D3D_DRIVER_TYPE_WARP, nullptr,
            flags, feature_levels, ARRAYSIZE(feature_levels),
            D3D11_SDK_VERSION, &wgc->device, &level, &wgc->context
        );
    }
    if (FAILED(hr)) { delete wgc; return nullptr; }

    ComPtr<IDXGIDevice> dxgi_device;
    hr = wgc->device.As(&dxgi_device);
    if (FAILED(hr)) { delete wgc; return nullptr; }

    typedef HRESULT (WINAPI *PFN_CreateDirect3D11DeviceFromDXGIDevice)(IDXGIDevice*, IInspectable**);
    HMODULE hD3D11 = GetModuleHandleW(L"d3d11.dll");
    if (!hD3D11) hD3D11 = LoadLibraryW(L"d3d11.dll");
    auto pfnCreate = (PFN_CreateDirect3D11DeviceFromDXGIDevice)GetProcAddress(hD3D11, "CreateDirect3D11DeviceFromDXGIDevice");
    if (!pfnCreate) { delete wgc; return nullptr; }

    ComPtr<IInspectable> inspectable;
    hr = pfnCreate(dxgi_device.Get(), &inspectable);
    if (FAILED(hr)) { delete wgc; return nullptr; }

    hr = inspectable.As(&wgc->winrt_device);
    if (FAILED(hr)) { delete wgc; return nullptr; }

    ComPtr<IGraphicsCaptureItemInterop> interop;
    hr = GetActivationFactory(L"Windows.Graphics.Capture.GraphicsCaptureItem", interop);
    if (FAILED(hr) || !interop) { delete wgc; return nullptr; }

    hr = interop->CreateForWindow(hwnd, IID_PPV_ARGS(&wgc->item));
    if (FAILED(hr) || !wgc->item) { delete wgc; return nullptr; }

    SizeInt32 size = { 0, 0 };
    wgc->item->get_Size(&size);
    if (size.Width <= 0 || size.Height <= 0) {
        RECT r = {};
        GetClientRect(hwnd, &r);
        size.Width = (r.right - r.left) > 0 ? (r.right - r.left) : 1280;
        size.Height = (r.bottom - r.top) > 0 ? (r.bottom - r.top) : 720;
    }
    wgc->width = size.Width;
    wgc->height = size.Height;

    ComPtr<IDirect3D11CaptureFramePoolStatics2> pool_statics;
    hr = GetActivationFactory(L"Windows.Graphics.Capture.Direct3D11CaptureFramePool", pool_statics);
    if (FAILED(hr) || !pool_statics) { delete wgc; return nullptr; }

    hr = pool_statics->CreateFreeThreaded(
        wgc->winrt_device.Get(),
        DirectXPixelFormat_B8G8R8A8UIntNormalized,
        2,
        size,
        &wgc->pool
    );
    if (FAILED(hr) || !wgc->pool) { delete wgc; return nullptr; }

    wgc->frame_event = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    auto handler = Callback<ITypedEventHandler<Direct3D11CaptureFramePool*, IInspectable*>>(
        [wgc](IDirect3D11CaptureFramePool*, IInspectable*) -> HRESULT {
            if (wgc->frame_event) SetEvent(wgc->frame_event);
            return S_OK;
        }
    );
    wgc->pool->add_FrameArrived(handler.Get(), &wgc->frame_token);

    hr = wgc->pool->CreateCaptureSession(wgc->item.Get(), &wgc->session);
    if (FAILED(hr) || !wgc->session) {
        CloseHandle(wgc->frame_event);
        delete wgc;
        return nullptr;
    }

    ComPtr<IGraphicsCaptureSession2> session2;
    if (SUCCEEDED(wgc->session.As(&session2))) {
        session2->put_IsCursorCaptureEnabled(capture_cursor ? TRUE : FALSE);
    }

    ComPtr<IGraphicsCaptureSession3> session3;
    if (SUCCEEDED(wgc->session.As(&session3))) {
        session3->put_IsBorderRequired(FALSE);
    }

    if (!CreateStagingTexture(wgc, wgc->width, wgc->height)) {
        CloseHandle(wgc->frame_event);
        delete wgc;
        return nullptr;
    }

    hr = wgc->session->StartCapture();
    if (FAILED(hr)) {
        CloseHandle(wgc->frame_event);
        delete wgc;
        return nullptr;
    }

    return wgc;
}

__declspec(dllexport) int luce_wgc_grab_frame(
    LuceWGC* wgc,
    uint32_t* out_w,
    uint32_t* out_h,
    uint8_t* out_buf,
    uint32_t buf_size,
    int timeout_ms
) {
    if (!wgc || !wgc->pool) return -1;

    if (wgc->frame_event && timeout_ms > 0) {
        WaitForSingleObject(wgc->frame_event, (DWORD)timeout_ms);
    }

    ComPtr<IDirect3D11CaptureFrame> frame;
    HRESULT hr = wgc->pool->TryGetNextFrame(&frame);
    if (FAILED(hr)) return -1;
    if (!frame) return 0; // Nenhum quadro novo

    ComPtr<IDirect3DSurface> surface;
    hr = frame->get_Surface(&surface);
    if (FAILED(hr) || !surface) return -1;

    ComPtr<IDirect3DDxgiInterfaceAccess> access;
    hr = surface.As(&access);
    if (FAILED(hr) || !access) return -1;

    ComPtr<ID3D11Texture2D> tex;
    hr = access->GetInterface(IID_PPV_ARGS(&tex));
    if (FAILED(hr) || !tex) return -1;

    D3D11_TEXTURE2D_DESC desc;
    tex->GetDesc(&desc);

    if (desc.Width != wgc->staging_width || desc.Height != wgc->staging_height) {
        if (!CreateStagingTexture(wgc, desc.Width, desc.Height)) return -1;
        wgc->width = desc.Width;
        wgc->height = desc.Height;
        SizeInt32 new_size = { (INT32)desc.Width, (INT32)desc.Height };
        wgc->pool->Recreate(wgc->winrt_device.Get(), DirectXPixelFormat_B8G8R8A8UIntNormalized, 2, new_size);
    }

    wgc->context->CopyResource(wgc->staging.Get(), tex.Get());

    D3D11_MAPPED_SUBRESOURCE mapped = {};
    hr = wgc->context->Map(wgc->staging.Get(), 0, D3D11_MAP_READ, 0, &mapped);
    if (FAILED(hr)) return -1;

    uint32_t needed = desc.Width * desc.Height * 4;
    if (buf_size < needed) {
        wgc->context->Unmap(wgc->staging.Get(), 0);
        *out_w = desc.Width;
        *out_h = desc.Height;
        return -2;
    }

    const uint8_t* src_row = (const uint8_t*)mapped.pData;
    uint8_t* dst_row = out_buf;
    uint32_t line_bytes = desc.Width * 4;
    if (mapped.RowPitch == line_bytes) {
        memcpy(dst_row, src_row, needed);
    } else {
        for (uint32_t y = 0; y < desc.Height; ++y) {
            memcpy(dst_row, src_row, line_bytes);
            src_row += mapped.RowPitch;
            dst_row += line_bytes;
        }
    }

    wgc->context->Unmap(wgc->staging.Get(), 0);

    *out_w = desc.Width;
    *out_h = desc.Height;
    return 1;
}

__declspec(dllexport) void luce_wgc_destroy(LuceWGC* wgc) {
    if (!wgc) return;

    if (wgc->session) {
        ComPtr<IClosable> closable;
        if (SUCCEEDED(wgc->session.As(&closable))) {
            closable->Close();
        }
        wgc->session.Reset();
    }
    if (wgc->pool) {
        if (wgc->frame_token.value != 0) {
            wgc->pool->remove_FrameArrived(wgc->frame_token);
        }
        ComPtr<IClosable> closable;
        if (SUCCEEDED(wgc->pool.As(&closable))) {
            closable->Close();
        }
        wgc->pool.Reset();
    }
    if (wgc->frame_event) {
        CloseHandle(wgc->frame_event);
        wgc->frame_event = nullptr;
    }
    wgc->staging.Reset();
    wgc->winrt_device.Reset();
    wgc->item.Reset();
    wgc->context.Reset();
    wgc->device.Reset();
    delete wgc;
}

}
