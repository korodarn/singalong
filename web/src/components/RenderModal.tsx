import { Component, createSignal, Show } from 'solid-js';
import { store } from '../state/store';
import { subscribeRenderSSE } from '../api';

export const RenderModal: Component = () => {
  const [previewSec, setPreviewSec] = createSignal<number | null>(null);
  const [assOnly, setAssOnly] = createSignal(false);
  const [isRunning, setIsRunning] = createSignal(false);
  const [logs, setLogs] = createSignal<string[]>([]);
  const [videoUrl, setVideoUrl] = createSignal<string | null>(null);
  let cancelSSE: (() => void) | null = null;

  const handleStart = () => {
    const vid = store.currentVid();
    if (!vid) return;

    setIsRunning(true);
    setVideoUrl(null);
    setLogs([`Starting render for ${vid}...`]);

    cancelSSE = subscribeRenderSSE(
      vid,
      previewSec(),
      assOnly(),
      (data) => {
        if (data.type === 'log') {
          setLogs((prev) => [...prev, data.text]);
        } else if (data.type === 'complete') {
          setIsRunning(false);
          setLogs((prev) => [...prev, `Render finished with return code ${data.returncode}`]);
          if (data.video_url) {
            setVideoUrl(data.video_url);
          }
        } else if (data.type === 'error') {
          setLogs((prev) => [...prev, `ERROR: ${data.message}`]);
          setIsRunning(false);
        }
      },
      (err) => {
        setLogs((prev) => [...prev, `SSE Connection Error: ${err}`]);
        setIsRunning(false);
      }
    );
  };

  const handleClose = () => {
    if (cancelSSE) cancelSSE();
    store.setShowRenderModal(false);
  };

  return (
    <Show when={store.showRenderModal()}>
      <div class="modal-backdrop" onClick={handleClose}>
        <div class="modal-card modal-large" onClick={(e) => e.stopPropagation()}>
          <div class="modal-header">
            <h3>Render Karaoke Video: {store.currentVid()}</h3>
            <button class="btn-close" onClick={handleClose}>✕</button>
          </div>
          <div class="modal-body">
            <div class="render-options">
              <label class="form-label">
                Render Duration:
                <select
                  class="form-input"
                  onChange={(e) => {
                    const val = e.currentTarget.value;
                    setPreviewSec(val ? Number(val) : null);
                  }}
                  disabled={isRunning()}
                >
                  <option value="">Full Song</option>
                  <option value="15">Quick Preview (First 15s)</option>
                  <option value="30">Quick Preview (First 30s)</option>
                  <option value="60">Quick Preview (First 60s)</option>
                </select>
              </label>

              <label class="form-checkbox">
                <input
                  type="checkbox"
                  checked={assOnly()}
                  onChange={(e) => setAssOnly(e.currentTarget.checked)}
                  disabled={isRunning()}
                />
                Generate .ass Subtitles only (Skip FFmpeg video encode)
              </label>
            </div>

            <Show when={videoUrl()}>
              <div class="rendered-video-preview">
                <video src={videoUrl()!} controls autoplay style={{ width: '100%', "max-height": '260px', "border-radius": '6px' }} />
              </div>
            </Show>

            <div class="terminal-log">
              {logs().map((l) => (
                <div class="log-line">{l}</div>
              ))}
            </div>
          </div>
          <div class="modal-footer">
            <button class="btn" onClick={handleClose} disabled={isRunning()}>Close</button>
            <button class="btn btn-success" onClick={handleStart} disabled={isRunning()}>
              {isRunning() ? 'Rendering Video...' : 'Start Render'}
            </button>
          </div>
        </div>
      </div>
    </Show>
  );
};
