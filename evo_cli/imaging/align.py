from evo_cli.imaging import load_numpy, load_pillow

SHIFT_GRID = 512
FIDELITY_GRID = 600
RIM_GRID = 800
OPAQUE_LEVEL = 250
RIM_LOW = 40
RIM_HIGH = 215
MIN_OPAQUE_RATIO = 0.02
MIN_RIM_PIXELS = 50
MAX_PSNR = 99.0


def _grey(image, grid):
    Image = load_pillow()
    numpy = load_numpy()
    return numpy.asarray(image.convert("L").resize((grid, grid), Image.LANCZOS), numpy.float32)


def _alpha(image, grid):
    Image = load_pillow()
    numpy = load_numpy()
    if "A" not in image.getbands():
        return None
    return numpy.asarray(image.convert("RGBA").resize((grid, grid), Image.LANCZOS))[:, :, 3]


def _signed(index, grid):
    return index - grid if index > grid // 2 else index


def find_shift(reference, candidate, grid=SHIFT_GRID):
    numpy = load_numpy()
    a = _grey(reference, grid)
    b = _grey(candidate, grid)
    a = a - a.mean()
    b = b - b.mean()
    spectrum = numpy.fft.fft2(a) * numpy.conj(numpy.fft.fft2(b))
    spectrum /= numpy.maximum(numpy.abs(spectrum), 1e-9)
    correlation = numpy.abs(numpy.fft.ifft2(spectrum))
    row, column = numpy.unravel_index(numpy.argmax(correlation), correlation.shape)
    width, height = reference.size
    dy = round(_signed(int(row), grid) * height / grid)
    dx = round(_signed(int(column), grid) * width / grid)
    return dy, dx, float(correlation.max())


def fidelity(reference, candidate, grid=FIDELITY_GRID):
    numpy = load_numpy()
    source = reference.convert("RGBA")
    a = _grey(source, grid)
    b = _grey(candidate, grid)
    alpha = _alpha(source, grid)
    mask = alpha > OPAQUE_LEVEL
    if mask.sum() < grid * grid * MIN_OPAQUE_RATIO:
        mask = numpy.ones(a.shape, bool)
    spread = a[mask].std() / max(float(b[mask].std()), 1e-6)
    levelled = (b - b[mask].mean()) * spread + a[mask].mean()
    error = float(((a[mask] - levelled[mask]) ** 2).mean())
    if error <= 0:
        return MAX_PSNR
    return min(float(10 * numpy.log10(255.0**2 / error)), MAX_PSNR)


def _rim_gap(image, alpha, grid):
    luma = _grey(image, grid)
    rim = (alpha > RIM_LOW) & (alpha < RIM_HIGH)
    body = alpha >= RIM_HIGH
    if rim.sum() < MIN_RIM_PIXELS or body.sum() < MIN_RIM_PIXELS:
        return None
    return float(luma[rim].mean() - luma[body].mean())


def rim_lift(reference, candidate, grid=RIM_GRID):
    source_alpha = _alpha(reference, grid)
    if source_alpha is None:
        return 0.0
    merged_alpha = _alpha(candidate, grid)
    if merged_alpha is None:
        merged_alpha = source_alpha
    source_gap = _rim_gap(reference, source_alpha, grid)
    merged_gap = _rim_gap(candidate, merged_alpha, grid)
    if source_gap is None or merged_gap is None:
        return 0.0
    return merged_gap - source_gap


def restore_alpha(source, candidate, shift=(0, 0)):
    numpy = load_numpy()
    Image = load_pillow()
    frame = source.convert("RGBA")
    merged = candidate.convert("RGB")
    if merged.size != frame.size:
        merged = merged.resize(frame.size, Image.LANCZOS)
    alpha = frame.split()[-1]
    dy, dx = int(shift[0]), int(shift[1])
    if dy or dx:
        rolled = numpy.roll(numpy.roll(numpy.asarray(alpha), -dy, 0), -dx, 1)
        alpha = Image.fromarray(rolled)
    merged = merged.convert("RGBA")
    merged.putalpha(alpha)
    return merged
