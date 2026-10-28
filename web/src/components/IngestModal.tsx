import { Component, createEffect, createSignal, Show } from 'solid-js';
import { store } from '../state/store';
import { subscribePipelineSSE } from '../api';

export const IngestModal: Component = () => {
  const [url, setUrl] = createSignal('');
  const [query, setQuery] = createSignal('');
  const [skipDemucs, setSkipDemucs] = createSignal(false);
  const [skipNetease, setSkipNetease] = createSignal(false);
  const [forceLrc, setForceLrc] = createSignal(false);

  const [isRunning, setIsRunning] = createSignal(false);
  const [activeStep, setActiveStep] = createSignal(0);
  const [logs, setLogs] = createSignal<string[]>([
    'Enter a YouTube Video ID (like XEGWxPmWTYs) and click [Run Pipeline & Create JSON].',
  ]);
  let cancelSSE: (() => void) | null = null;
  let logContainerRef!: HTMLDivElement;

  createEffect(() => {
    if (store.showIngestModal()) {
      if (store.headerInputVid()) {
        setUrl(store.headerInputVid());
      }
    }
  });

  const appendLog = (text: string) => {
    setLogs((prev) => [...prev, text]);
    setTimeout(() => {
      if (logContainerRef) logContainerRef.scrollTop = logContainerRef.scrollHeight;
    }, 10);
  };

  const handleStart = () => {
    const targetUrl = url().trim();
    if (!targetUrl) return;

    setIsRunning(true);
    setActiveStep(1);
    setLogs([`[*] Starting Singalong Pipeline for: ${targetUrl}`]);

    cancelSSE = subscribePipelineSSE(
      {
        url: targetUrl,
        query: query().trim() || undefined,
        skip_demucs: skipDemucs(),
        skip_netease: skipNetease(),
        force_lrc: forceLrc(),
      },
      async (data) => {
        if (data.type === 'start') {
          appendLog(`$ ${data.command}\n`);
        } else if (data.type === 'log') {
          const line = data.text || '';
          appendLog(line);
          if (line.includes('[1/4]')) setActiveStep(1);
          else if (line.includes('[2/4]')) setActiveStep(2);
          else if (line.includes('[3/4]')) setActiveStep(3);
          else if (line.includes('[4/4]')) setActiveStep(4);
        } else if (data.type === 'complete') {
          setIsRunning(false);
          if (data.returncode === 0 && data.video_id) {
            setActiveStep(5);
            appendLog(`\n[✅] Pipeline finished! Loading ${data.video_id}.json into Workbench...`);
            await store.loadProjectList();
            await store.selectProject(data.video_id);
            setTimeout(() => store.setShowIngestModal(false), 800);
          } else {
            appendLog(`\n[!] Pipeline exited with code ${data.returncode}.`);
          }
        } else if (data.type === 'error') {
          appendLog(`\n[!] Error: ${data.message}`);
          setIsRunning(false);
        }
      },
      (err) => {
        appendLog(`\n[!] SSE Connection Error: ${err}`);
        setIsRunning(false);
      }
    );
  };

  const handleClose = () => {
    if (cancelSSE) cancelSSE();
    setIsRunning(false);
    store.setShowIngestModal(false);
  };

  const stepClass = (stepNum: number) => {
    const s = activeStep();
    if (s > stepNum) return 'step-box done';
    if (s === stepNum) return 'step-box active';
    return 'step-box';
  };

  return (
    <Show when={store.showIngestModal()}>
      <div class="modal-backdrop" onClick={handleClose}>
        <div class="modal-card modal-large" onClick={(e) => e.stopPropagation()}>
          <div class="modal-header">
            <h3>🚀 YouTube Import & Alignment Pipeline (singalong.py)</h3>
            <button class="btn-close" onClick={handleClose}>✕</button>
          </div>
          <div class="modal-body">
            <label class="form-label">
              YouTube Video Code (11-char ID) or Full YouTube URL:
              <input
                class="form-input"
                placeholder="e.g. XEGWxPmWTYs or https://www.youtube.com/watch?v=XEGWxPmWTYs"
                value={url()}
                onInput={(e) => setUrl(e.currentTarget.value)}
                disabled={isRunning()}
              />
            </label>
            <label class="form-label">
              Custom Lyrics Search Query (Optional — leave blank to use YouTube title):
              <input
                class="form-input"
                placeholder="e.g. No One Told Me Wuthering Waves"
                value={query()}
                onInput={(e) => setQuery(e.currentTarget.value)}
                disabled={isRunning()}
              />
            </label>
            <div class="checkbox-row">
              <label class="form-checkbox">
                <input
                  type="checkbox"
                  checked={forceLrc()}
                  onChange={(e) => setForceLrc(e.currentTarget.checked)}
                  disabled={isRunning()}
                />
                Force Re-fetch NetEase Lyrics
              </label>
              <label class="form-checkbox">
                <input
                  type="checkbox"
                  checked={skipDemucs()}
                  onChange={(e) => setSkipDemucs(e.currentTarget.checked)}
                  disabled={isRunning()}
                />
                Skip Demucs Vocal Split (Faster)
              </label>
              <label class="form-checkbox">
                <input
                  type="checkbox"
                  checked={skipNetease()}
                  onChange={(e) => setSkipNetease(e.currentTarget.checked)}
                  disabled={isRunning()}
                />
                Unconstrained ASR Only (Skip LRC)
              </label>
            </div>

            <div class="pipeline-steps">
              <div class={stepClass(1)}>1. Audio Download</div>
              <div class={stepClass(2)}>2. Demucs Vocals</div>
              <div class={stepClass(3)}>3. FunASR Stopwatch</div>
              <div class={stepClass(4)}>4. Lyric Alignment</div>
            </div>

            <div ref={logContainerRef} class="terminal-log">
              {logs().map((l) => (
                <div class="log-line">{l}</div>
              ))}
            </div>
          </div>
          <div class="modal-footer">
            <button class="btn" onClick={handleClose} disabled={isRunning()}>Close</button>
            <button class="btn btn-lead" onClick={handleStart} disabled={isRunning() || !url().trim()}>
              {isRunning() ? '⏳ Running Pipeline...' : '⚡ Run Pipeline & Create JSON'}
            </button>
          </div>
        </div>
      </div>
    </Show>
  );
};
