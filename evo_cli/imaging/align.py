from evo_cli.imaging import load_numpy, load_pillow

SHIFT_GRID = 512
FIDELITY_GRID = 600
RIM_GRID = 800
OPAQUE_LEVEL = 250
RIM_LOW = 40
RIM_HIGH = 215
CLEAR_LEVEL = 12
MIN_COVERAGE = 0.15
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


def _rim_gap(luma, alpha):
    rim = (alpha > RIM_LOW) & (alpha < RIM_HIGH)
    body = alpha >= RIM_HIGH
    if rim.sum() < MIN_RIM_PIXELS or body.sum() < MIN_RIM_PIXELS:
        return None
    return float(luma[rim].mean() - luma[body].mean())


def _backdrop(luma, alpha):
    numpy = load_numpy()
    clear = alpha <= CLEAR_LEVEL
    if clear.sum() < MIN_RIM_PIXELS:
        return None
    return float(numpy.median(luma[clear]))


def _straight(luma, alpha, backdrop):
    numpy = load_numpy()
    coverage = numpy.maximum(alpha.astype(numpy.float32) / 255.0, MIN_COVERAGE)
    return numpy.clip((luma - (1.0 - coverage) * backdrop) / coverage, 0.0, 255.0)


def _aligned(alpha, shift, size, grid):
    numpy = load_numpy()
    width, height = size
    dy = round(shift[0] * grid / height)
    dx = round(shift[1] * grid / width)
    if not (dy or dx):
        return alpha
    return numpy.roll(numpy.roll(alpha, -dy, 0), -dx, 1)


def rim_lift(reference, candidate, shift=(0, 0), grid=RIM_GRID):
    alpha = _alpha(reference, grid)
    if alpha is None:
        return 0.0
    source_gap = _rim_gap(_grey(reference, grid), alpha)
    rim_alpha = _aligned(alpha, shift, reference.size, grid)
    luma = _grey(candidate, grid)
    if "A" not in candidate.getbands():
        backdrop = _backdrop(luma, rim_alpha)
        if backdrop is not None:
            luma = _straight(luma, rim_alpha, backdrop)
    candidate_gap = _rim_gap(luma, rim_alpha)
    if source_gap is None or candidate_gap is None:
        return 0.0
    return candidate_gap - source_gap


def carry_alpha(alpha, candidate):
    Image = load_pillow()
    merged = candidate.convert("RGBA")
    band = alpha.convert("L")
    if band.size != merged.size:
        band = band.resize(merged.size, Image.LANCZOS)
    merged.putalpha(band)
    return merged


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
    return carry_alpha(alpha, merged)
