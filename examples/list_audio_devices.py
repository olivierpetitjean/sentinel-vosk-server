import sounddevice as sd

RATES_TO_CHECK = [44100, 48000]

def supports(device_idx: int, samplerate: int, channels: int, dtype: str = "int16") -> bool:
    try:
        sd.check_input_settings(device=device_idx, samplerate=samplerate, channels=channels, dtype=dtype)
        return True
    except Exception:
        return False

def main() -> None:
    print("Input devices:")
    for i, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] <= 0:
            continue

        supported = []
        # probe mono first, then stereo as fallback
        for r in RATES_TO_CHECK:
            if supports(i, r, channels=1) or supports(i, r, channels=2):
                supported.append(r)

        can_16k = supports(i, 16000, channels=1) or supports(i, 16000, channels=2)

        print(f"  [{i}] {dev['name']}")
        print(f"      max_in_ch={dev['max_input_channels']} default_sr={dev.get('default_samplerate')}")
        print(f"      supported_rates={supported}  supports_16000={can_16k}")

if __name__ == "__main__":
    main()
