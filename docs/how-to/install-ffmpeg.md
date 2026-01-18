# Install ffmpeg with SRT Support

popup-ai uses ffmpeg to receive audio streams via SRT (Secure Reliable Transport) protocol. The standard Homebrew ffmpeg does **not** include SRT support, so you need to install a custom build.

## Quick Install (macOS)

```bash
# Add the homebrew-ffmpeg tap
brew tap homebrew-ffmpeg/ffmpeg

# Install with SRT support
brew install homebrew-ffmpeg/ffmpeg/ffmpeg --with-srt
```

## If You Already Have ffmpeg Installed

```bash
# Unlink the existing ffmpeg
brew unlink ffmpeg

# Install the custom build with SRT
brew install homebrew-ffmpeg/ffmpeg/ffmpeg --with-srt
```

## Verify Installation

Check that SRT protocol is available:

```bash
ffmpeg -protocols 2>&1 | grep -E "^\s+srt$"
```

You should see `srt` in the output. If not, the installation didn't include SRT support.

Check the version and build configuration:

```bash
ffmpeg -version
```

Look for `--enable-libsrt` in the configuration line.

## Recommended Build Options

For popup-ai, the minimal install is:

```bash
brew install homebrew-ffmpeg/ffmpeg/ffmpeg --with-srt
```

For better audio quality, add these options:

```bash
brew install homebrew-ffmpeg/ffmpeg/ffmpeg \
    --with-srt \
    --with-fdk-aac \
    --with-libsoxr
```

| Option | Purpose |
|--------|---------|
| `--with-srt` | **Required.** SRT protocol for OBS streaming |
| `--with-fdk-aac` | Higher quality AAC codec (if OBS uses AAC audio) |
| `--with-libsoxr` | High-quality audio resampling |

## All Available Options

To see all available build options:

```bash
brew options homebrew-ffmpeg/ffmpeg/ffmpeg
```

Notable options for audio/video work:

| Option | Description |
|--------|-------------|
| `--with-srt` | SRT streaming protocol |
| `--with-fdk-aac` | Fraunhofer AAC codec |
| `--with-libsoxr` | High-quality resampling |
| `--with-openssl` | SSL/TLS support |
| `--with-librist` | RIST streaming protocol |
| `--with-whisper-cpp` | Whisper speech recognition (alternative to mlx-whisper) |

## Troubleshooting

### "Protocol not found" Error

If you see this error when running popup-ai:

```
[ffmpeg] Error opening input: Protocol not found
[ffmpeg] Error opening input file srt://0.0.0.0:9998?mode=listener&latency=200000
```

Your ffmpeg does not have SRT support. Reinstall using the instructions above.

### Brew Conflicts

If you get conflicts with the standard ffmpeg:

```bash
# Remove the standard ffmpeg completely
brew uninstall ffmpeg

# Then install the custom build
brew install homebrew-ffmpeg/ffmpeg/ffmpeg --with-srt
```

### Build Failures

If the build fails, ensure you have Xcode command line tools:

```bash
xcode-select --install
```

And accept the Xcode license:

```bash
sudo xcodebuild -license accept
```

## Linux Installation

On Ubuntu/Debian, ffmpeg typically includes SRT support:

```bash
sudo apt update
sudo apt install ffmpeg libsrt-dev
```

Verify:

```bash
ffmpeg -protocols 2>&1 | grep srt
```

If SRT is missing, you may need to build from source with `--enable-libsrt`.

## Windows Installation

Use the full build from gyan.dev which includes SRT:

1. Download from: https://www.gyan.dev/ffmpeg/builds/
2. Choose the "full" build (not essentials)
3. Extract and add to PATH

Or use winget:

```powershell
winget install ffmpeg
```

## See Also

- [Quickstart](../tutorials/quickstart.md) - Get started with popup-ai
- [Configure OBS](configure-obs.md) - Set up OBS for SRT streaming
- [Architecture](../explanation/architecture.md) - How the audio pipeline works
