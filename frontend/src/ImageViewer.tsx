import { useLayoutEffect, useRef, useState } from "react";
import { useElementSize } from "@mantine/hooks";
import { useQuery } from "@tanstack/react-query";
import {
  ChevronLeft,
  ChevronRight,
  RotateCcw,
  RotateCw,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import { CopyImageText } from "./CopyImageText";
import { api } from "./api";
import "./image-viewer.css";

export type DialogImage = { id: string; name: string; url: string };
const scales = [0.25, 0.5, 0.75, 1, 1.25, 1.5, 2, 3, 4];
const fitIndex = scales.indexOf(1);

function ImageCanvas({ image }: { image: DialogImage }) {
  const imageRef = useRef<HTMLImageElement>(null);
  const { ref, width, height } = useElementSize();
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [angle, setAngle] = useState(0);
  const [zoom, setZoom] = useState(fitIndex);
  const [failedUrl, setFailedUrl] = useState("");
  const sideways = angle % 180 !== 0;
  const boxWidth = sideways ? size.h : size.w;
  const boxHeight = sideways ? size.w : size.h;
  const fit =
    boxWidth && boxHeight
      ? Math.min(1, width / boxWidth, height / boxHeight)
      : 0;
  const scale = fit * scales[zoom];

  useLayoutEffect(() => {
    const el = ref.current;
    if (el) {
      el.scrollLeft = (el.scrollWidth - el.clientWidth) / 2;
      el.scrollTop = (el.scrollHeight - el.clientHeight) / 2;
    }
  }, [ref, scale, angle]);

  return (
    <>
      <div className="gallery-viewport" ref={ref}>
        {failedUrl === image.url ? (
          <span role="status">
            Не удалось загрузить изображение.{" "}
            <button onClick={() => setFailedUrl("")}>Повторить</button>
          </span>
        ) : (
          <div
            className="gallery-frame"
            style={{ width: boxWidth * scale, height: boxHeight * scale }}
          >
            <img
              ref={imageRef}
              src={image.url}
              alt={image.name || "Изображение"}
              draggable={false}
              style={{
                width: size.w * scale,
                height: size.h * scale,
                transform: `translate(-50%, -50%) rotate(${angle}deg)`,
              }}
              onLoad={(e) =>
                setSize({
                  w: e.currentTarget.naturalWidth,
                  h: e.currentTarget.naturalHeight,
                })
              }
              onError={() => setFailedUrl(image.url)}
            />
          </div>
        )}
      </div>
      <div
        className="gallery-tools"
        role="group"
        aria-label="Действия с изображением"
      >
        <button
          aria-label="Повернуть влево"
          title="Повернуть влево на 90°"
          onClick={() => setAngle((a) => (a + 270) % 360)}
        >
          <RotateCcw />
        </button>
        <button
          aria-label="Повернуть вправо"
          title="Повернуть вправо на 90°"
          onClick={() => setAngle((a) => (a + 90) % 360)}
        >
          <RotateCw />
        </button>
        <button
          aria-label="Уменьшить"
          title="Уменьшить"
          disabled={zoom === 0}
          onClick={() => setZoom(zoom - 1)}
        >
          <ZoomOut />
        </button>
        <button
          className="gallery-scale"
          title="По размеру окна"
          aria-label="По размеру окна"
          onClick={() => setZoom(fitIndex)}
        >
          {scales[zoom] * 100}%
        </button>
        <button
          aria-label="Увеличить"
          title="Увеличить"
          disabled={zoom === scales.length - 1}
          onClick={() => setZoom(zoom + 1)}
        >
          <ZoomIn />
        </button>
        <CopyImageText
          key={angle}
          image={imageRef}
          angle={angle}
          disabled={!size.w || failedUrl === image.url}
        />
      </div>
    </>
  );
}

export function ImageViewer({
  conversationId,
  initialImage,
  onClose,
}: {
  conversationId: string;
  initialImage: DialogImage;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [activeId, setActiveId] = useState(initialImage.id);
  const gallery = useQuery({
    queryKey: ["conversation-images", conversationId],
    queryFn: () =>
      api<DialogImage[]>(`/api/conversations/${conversationId}/images`),
    gcTime: 0,
    refetchInterval: 4 * 60_000, // The file URLs expire after ten minutes.
  });
  const images = gallery.data ?? [initialImage];
  const index = Math.max(
    0,
    images.findIndex((image) => image.id === activeId),
  );
  const image = images[index];
  function move(step: number) {
    const next = images[index + step];
    if (next) setActiveId(next.id);
  }

  useLayoutEffect(() => {
    const el = dialog.current!;
    el.showModal();
    return () => el.close();
  }, []);

  return (
    <dialog
      ref={dialog}
      className="crm-image-viewer"
      aria-label="Изображения диалога"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        if (event.target === dialog.current) onClose();
      }}
      onKeyDown={(event) => {
        if (event.target instanceof HTMLTextAreaElement) return;
        if (event.altKey || event.ctrlKey || event.metaKey) return;
        if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
          event.preventDefault();
          event.stopPropagation();
          move(event.key === "ArrowLeft" ? -1 : 1);
        }
      }}
    >
      <div className="gallery-panel">
        <header>
          <span className="gallery-name" title={image?.name}>
            {image?.name || "Изображение"}
          </span>
          <div className="gallery-nav">
            <button
              aria-label="Предыдущее изображение"
              title="Предыдущее изображение (←)"
              disabled={!index}
              onClick={() => move(-1)}
            >
              <ChevronLeft />
            </button>
            <span aria-live="polite">
              {gallery.data
                ? `${image ? index + 1 : 0} / ${images.length}`
                : "…"}
            </span>
            <button
              aria-label="Следующее изображение"
              title="Следующее изображение (→)"
              disabled={index + 1 >= images.length}
              onClick={() => move(1)}
            >
              <ChevronRight />
            </button>
          </div>
          <button
            autoFocus
            aria-label="Закрыть просмотр"
            title="Закрыть (Esc)"
            onClick={onClose}
          >
            <X />
          </button>
        </header>
        {gallery.isError && (
          <div role="alert">
            Не удалось обновить список изображений.{" "}
            <button onClick={() => void gallery.refetch()}>Повторить</button>
          </div>
        )}
        {image ? (
          <ImageCanvas key={image.id} image={image} />
        ) : (
          <p role="status">В диалоге нет доступных изображений</p>
        )}
      </div>
    </dialog>
  );
}
