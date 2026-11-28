import { PSM, type Page, type Word, type Worker } from "tesseract.js";

function rotate(source: OffscreenCanvas, degrees: number) {
  if (degrees === 0) return source;
  const sideways = degrees % 180 !== 0;
  const canvas = new OffscreenCanvas(
    sideways ? source.height : source.width,
    sideways ? source.width : source.height,
  );
  const ctx = canvas.getContext("2d")!;
  ctx.translate(canvas.width / 2, canvas.height / 2);
  ctx.rotate((degrees * Math.PI) / 180);
  ctx.drawImage(source, -source.width / 2, -source.height / 2);
  return canvas;
}

function grayscale(pixels: Uint8ClampedArray, i: number) {
  return Math.round(
    0.299 * pixels[i] + 0.587 * pixels[i + 1] + 0.114 * pixels[i + 2],
  );
}

function percentile(histogram: Uint32Array, count: number, fraction: number) {
  let total = 0;
  for (let i = 0; i < histogram.length; i++) {
    total += histogram[i];
    if (total >= count * fraction) return i;
  }
  return 255;
}

// Keep glyph-sized connected regions. Large reflections and label borders interfere
// with sparse-text segmentation on photos of dark metal or glossy packaging.
function clearBackground(mask: Uint8Array, width: number, height: number) {
  const queue = new Int32Array(mask.length);
  const limit = Math.max(width, height) * 0.085;
  for (let i = 0; i < mask.length; i++) {
    if (mask[i] !== 1) continue;
    let end = 1;
    queue[0] = i;
    mask[i] = 2;
    let minX = width,
      minY = height,
      maxX = 0,
      maxY = 0;
    for (let head = 0; head < end; head++) {
      const at = queue[head],
        x = at % width,
        y = Math.floor(at / width);
      minX = Math.min(minX, x);
      maxX = Math.max(maxX, x);
      minY = Math.min(minY, y);
      maxY = Math.max(maxY, y);
      for (
        let ny = Math.max(0, y - 1);
        ny <= Math.min(height - 1, y + 1);
        ny++
      ) {
        for (
          let nx = Math.max(0, x - 1);
          nx <= Math.min(width - 1, x + 1);
          nx++
        ) {
          const next = ny * width + nx;
          if (mask[next] === 1) {
            mask[next] = 2;
            queue[end++] = next;
          }
        }
      }
    }
    const w = maxX - minX + 1,
      h = maxY - minY + 1;
    if (
      end < 8 ||
      Math.min(w, h) < 2 ||
      Math.max(w, h) > limit ||
      end < w * h * 0.08
    ) {
      for (let j = 0; j < end; j++) mask[queue[j]] = 0;
    }
  }
}

function prepare(source: OffscreenCanvas) {
  const w = source.width,
    h = source.height,
    count = w * h;
  const ctx = source.getContext("2d")!;
  const pixels = ctx.getImageData(0, 0, w, h);
  const gray = new Uint8Array(count);
  const hist = new Uint32Array(256);
  for (let i = 0; i < count; i++) {
    gray[i] = grayscale(pixels.data, i * 4);
    hist[gray[i]]++;
  }
  if (hist.some((n) => n === count)) return null;
  const brightText = percentile(hist, count, 0.5) < 128;
  const threshold = (fraction: number) =>
    percentile(hist, count, brightText ? fraction : 1 - fraction);
  const cutoff = threshold(0.83);
  const mask = gray.map((v) => Number(brightText ? v > cutoff : v < cutoff));
  clearBackground(mask, w, h);
  const clean = new OffscreenCanvas(w, h);
  for (let i = 0; i < count; i++) {
    const v = mask[i] ? 0 : 255;
    pixels.data[i * 4] = pixels.data[i * 4 + 1] = pixels.data[i * 4 + 2] = v;
  }
  clean.getContext("2d")!.putImageData(pixels, 0, 0);
  return { clean, brightText, threshold };
}

function candidate(page: Page) {
  const words = (page.blocks || []).flatMap((b) =>
    b.paragraphs.flatMap((p) => p.lines.flatMap((l) => l.words)),
  );
  // Compare the amount of confidently readable text. A handful of stray high
  // confidence digits can have a better page average than an entire label.
  let score = 0;
  for (const word of words) {
    const n = word.text.match(/[\p{L}\p{N}]/gu)?.length || 0;
    if (n > 1)
      score += n * Math.pow(Math.max(0, (word.confidence - 45) / 55), 2);
  }
  return { text: page.text.trim(), confidence: page.confidence, words, score };
}

function reliable(result: ReturnType<typeof candidate>) {
  return result.confidence >= 80 && result.score >= 3;
}

async function readCode(
  worker: Worker,
  source: OffscreenCanvas,
  word: Word,
  cutoff: number,
  brightText: boolean,
) {
  const { x0, y0, x1, y1 } = word.bbox;
  // Stay close to the detected word to exclude neighboring rows and reflections.
  const pad = Math.min(3, Math.round((y1 - y0) * 0.06));
  const x = Math.max(0, x0 - pad),
    y = Math.max(0, y0 - pad);
  const w = Math.min(source.width, x1 + pad) - x,
    h = Math.min(source.height, y1 + pad) - y;
  const crop = new OffscreenCanvas(w, h);
  const pixels = source.getContext("2d")!.getImageData(x, y, w, h);
  for (let i = 0; i < pixels.data.length; i += 4) {
    const g = grayscale(pixels.data, i);
    const value = (brightText ? g > cutoff : g < cutoff) ? 0 : 255;
    pixels.data[i] = pixels.data[i + 1] = pixels.data[i + 2] = value;
  }
  crop.getContext("2d")!.putImageData(pixels, 0, 0);
  const scale = Math.min(3, 2048 / w, 512 / h);
  const zoom = new OffscreenCanvas(
    Math.max(1, Math.round(w * scale)),
    Math.max(1, Math.round(h * scale)),
  );
  zoom.getContext("2d")!.drawImage(crop, 0, 0, zoom.width, zoom.height);
  const { data } = await worker.recognize(await zoom.convertToBlob());
  return data.text.replace(/\s/g, "");
}

export async function recognizePhoto(worker: Worker, blob: Blob) {
  const output = { text: true, blocks: true };
  await worker.setParameters({ tessedit_pageseg_mode: PSM.AUTO });
  let best = candidate((await worker.recognize(blob, {}, output)).data);
  const result = () => ({
    text: best.text,
    uncertain: !!best.text && !reliable(best),
  });
  if (
    reliable(best) ||
    typeof OffscreenCanvas === "undefined" ||
    typeof createImageBitmap === "undefined"
  )
    return result();

  // Preserve the best completed pass if a low-memory device cannot enhance the photo.
  const deadline = performance.now() + 45_000;
  try {
    const bitmap = await createImageBitmap(blob);
    const scale = Math.min(1, 1600 / Math.max(bitmap.width, bitmap.height));
    const source = new OffscreenCanvas(
      Math.max(1, Math.round(bitmap.width * scale)),
      Math.max(1, Math.round(bitmap.height * scale)),
    );
    source
      .getContext("2d", { willReadFrequently: true })!
      .drawImage(bitmap, 0, 0, source.width, source.height);
    bitmap.close();
    const photo = prepare(source);
    if (!photo) return result();
    let enhancedAngle: number | null = null;
    for (const angle of [90, 270, 180]) {
      if (performance.now() >= deadline) return result();
      const raw = candidate(
        (
          await worker.recognize(
            await rotate(source, angle).convertToBlob(),
            {},
            output,
          )
        ).data,
      );
      if (reliable(raw)) return { text: raw.text, uncertain: false };
      if (raw.score > best.score) best = raw;
    }
    await worker.setParameters({ tessedit_pageseg_mode: PSM.SPARSE_TEXT });
    for (const angle of [0, 90, 270, 180]) {
      if (performance.now() >= deadline) return result();
      const page = (
        await worker.recognize(
          await rotate(photo.clean, angle).convertToBlob(),
          {},
          output,
        )
      ).data;
      const next = candidate(page);
      if (next.score > best.score || (!best.text && next.text)) {
        best = next;
        enhancedAngle = angle;
      }
    }
    if (enhancedAngle !== null) {
      const codes = best.words.filter(
        (w) =>
          w.confidence < 85 &&
          /^(?=.*[A-Z])(?=.*\d)[A-Z0-9]{10,24}$/.test(w.text),
      );
      if (codes.length) {
        const upright = rotate(source, enhancedAngle);
        await worker.reinitialize("eng");
        await worker.setParameters({
          tessedit_pageseg_mode: PSM.SINGLE_WORD,
          tessedit_char_whitelist: "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        });
        for (const word of codes.slice(0, 4)) {
          if (performance.now() >= deadline) break;
          if (best.text.split(word.text).length !== 2) continue;
          const a = await readCode(
            worker,
            upright,
            word,
            photo.threshold(0.83),
            photo.brightText,
          );
          if (
            a === word.text ||
            a.length !== word.text.length ||
            !/^[A-Z0-9]+$/.test(a)
          )
            continue;
          const differences = [...a].filter(
            (c, i) => c !== word.text[i],
          ).length;
          if (differences > 2) continue;
          const b = await readCode(
            worker,
            upright,
            word,
            photo.threshold(0.88),
            photo.brightText,
          );
          if (a === b) best.text = best.text.replace(word.text, a);
        }
      }
    }
  } catch {
    return result();
  }
  return result();
}
