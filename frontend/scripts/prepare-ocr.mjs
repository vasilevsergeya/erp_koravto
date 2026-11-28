import { copyFileSync, mkdirSync } from "node:fs";

const target = "public/ocr";
mkdirSync(target, { recursive: true });

function copy(pkg, path) {
  copyFileSync(
    `node_modules/${pkg}/${path}`,
    `${target}/${path.split("/").pop()}`,
  );
}

copy("tesseract.js", "dist/worker.min.js");
copy("tesseract.js", "dist/worker.min.js.LICENSE.txt");
for (const variant of ["", "-simd", "-relaxedsimd"]) {
  copy("tesseract.js-core", `tesseract-core${variant}-lstm.wasm.js`);
}
for (const lang of ["eng", "rus", "kor"]) {
  copy(`@tesseract.js-data/${lang}`, `4.0.0_best_int/${lang}.traineddata.gz`);
}
