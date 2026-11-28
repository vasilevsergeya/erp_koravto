import { createWorker, OEM } from "tesseract.js";

import { recognizePhoto } from "./photoOcr";

// The viewer owns this worker and its child, including while language data is loading.
self.onmessage = async ({ data }: MessageEvent<Blob>) => {
  let ready = false;
  try {
    const worker = await createWorker(["eng", "rus", "kor"], OEM.LSTM_ONLY, {
      workerPath: "/ocr/worker.min.js",
      workerBlobURL: false,
      corePath: "/ocr",
      langPath: "/ocr",
      errorHandler: () => {
        if (!ready) self.postMessage({ error: true });
      },
    });
    ready = true;
    self.postMessage(await recognizePhoto(worker, data));
  } catch {
    self.postMessage({ error: true });
  }
};
