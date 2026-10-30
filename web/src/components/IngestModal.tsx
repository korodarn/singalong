import { Component, createEffect, createSignal, For, Show } from 'solid-js';
import { store } from '../state/store';
import { prepareLyrics, searchLyrics, subscribePipelineSSE, validateLyrics } from '../api';
import { LyricsCandidate } from '../types';

export const IngestModal: Component = () => {
  const [url, setUrl] = createSignal('');
  const [lastResetUrl, setLastResetUrl] = createSignal('');
  const [customQuery, setCustomQuery] = createSignal('');
  const [targetLang, setTargetLang] = createSignal('auto');
  const [asrEngine, setAsrEngine] = createSignal('auto');

  // 2-Step Workflow stage: 'step1' (Audio + Discovery) -> 'step2' (Lyrics Preview & Confirm Alignment)
  const [stage, setStage] = createSignal<'step1' | 'step2'>('step1');
  const [resolvedVid, setResolvedVid] = createSignal('');
  const [songTitle, setSongTitle] = createSignal('');

  // Candidates & Editable Preview state
  const [isSearching, setIsSearching] = createSignal(false);
  const [candidates, setCandidates] = createSignal<LyricsCandidate[]>([]);
  const [selectedCandId, setSelectedCandId] = createSignal<string | null>(null);
  const [editableLyrics, setEditableLyrics] = createSignal('');
  const [liveValidation, setLiveValidation] = createSignal<any | null>(null);

  // Pipeline options & state
  const [skipDemucs, setSkipDemucs] = createSignal(false);
  const [forceLrc, setForceLrc] = createSignal(false);
  const [forceAsr, setForceAsr] = createSignal(false);
  const [pureAsrOnly, setPureAsrOnly] = createSignal(false);
  const [isRunning, setIsRunning] = createSignal(false);
  const [activeStep, setActiveStep] = createSignal(0);
  const [logs, setLogs] = createSignal<string[]>([
    'Step 1: Fetch audio & discover sense-checked lyrics candidates. Step 2: Preview/edit lyrics before alignment.',
  ]);

  let cancelSSE: (() => void) | null = null;
  let logContainerRef!: HTMLDivElement;

  const resetSongState = (newUrl: string) => {
    setStage('step1');
    setResolvedVid('');
    setSongTitle('');
    setCandidates([]);
    setSelectedCandId(null);
    setEditableLyrics('');
    setLiveValidation(null);
    setActiveStep(0);
    setLastResetUrl(newUrl);
  };

  createEffect(() => {
    if (store.showIngestModal()) {
      const headerVid = store.headerInputVid().trim();
      if (headerVid && headerVid !== lastResetUrl()) {
        setUrl(headerVid);
        resetSongState(headerVid);
      }
    }
  });

  const handleUrlChange = (val: string) => {
    setUrl(val);
    if (val.trim() !== lastResetUrl()) {
      resetSongState(val.trim());
    }
  };

  const appendLog = (text: string) => {
    setLogs((prev) => [...prev, text]);
    setTimeout(() => {
      if (logContainerRef) logContainerRef.scrollTop = logContainerRef.scrollHeight;
    }, 10);
  };

  const selectCandidateForPreview = (cand: LyricsCandidate) => {
    setSelectedCandId(cand.id);
    const textToPreview = (cand.clean_lyrics || cand.raw_lyrics || '').trim();
    setEditableLyrics(textToPreview);
    handleValidatePreviewText(textToPreview);
  };

  const applyCandidatesList = (cands: LyricsCandidate[], title?: string, vid?: string) => {
    setCandidates(cands);
    if (title) setSongTitle(title);
    if (vid) setResolvedVid(vid);

    const topValid = cands.find((c) => c.validation.is_valid) || cands[0];
    if (topValid) {
      selectCandidateForPreview(topValid);
    }
  };

  const handleQuickSearchLyrics = async () => {
    const targetUrl = url().trim();
    const query = customQuery().trim();
    if (!targetUrl && !query) return;

    setIsSearching(true);
    appendLog(`[*] Searching lyrics databases for "${query || targetUrl}"...`);

    try {
      const res = await searchLyrics(query, targetUrl, targetLang() !== 'auto' ? targetLang() : undefined);
      if (res.ok && res.candidates) {
        applyCandidatesList(res.candidates);
        setStage('step2');
        appendLog(`[✓] Found ${res.candidates.length} candidate(s). Review lyrics in the preview box below.`);
      }
    } catch (e: any) {
      appendLog(`[!] Lyrics search failed: ${e.message}`);
    } finally {
      setIsSearching(false);
    }
  };

  const handleValidatePreviewText = async (text: string) => {
    setEditableLyrics(text);
    if (!text.trim()) {
      setLiveValidation(null);
      return;
    }
    try {
      const res = await validateLyrics(text, targetLang() !== 'auto' ? targetLang() : undefined);
      if (res.ok) {
        setLiveValidation(res);
      }
    } catch (e) {
      // ignore
    }
  };

  // STEP 1: Run Audio + Demucs + ASR + Lyrics Discovery (--preview-only)
  const handleRunStep1Preview = async () => {
    const targetUrl = url().trim();
    if (!targetUrl) return;

    setIsRunning(true);
    setActiveStep(1);
    setLogs([`[*] Step 1: Running Audio + ASR + Lyrics Discovery for: ${targetUrl}`]);

    cancelSSE = subscribePipelineSSE(
      {
        url: targetUrl,
        query: customQuery().trim() || undefined,
        target_lang: targetLang() !== 'auto' ? targetLang() : undefined,
        asr_engine: asrEngine(),
        skip_demucs: skipDemucs(),
        skip_netease: pureAsrOnly(),
        force_lrc: forceLrc(),
        force_asr: forceAsr(),
        preview_only: true,
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
        } else if (data.type === 'complete') {
          setIsRunning(false);
          if (data.video_id) setResolvedVid(data.video_id);

          if (data.preview && data.preview.candidates) {
            applyCandidatesList(data.preview.candidates, data.preview.title, data.video_id);
            setStage('step2');
            appendLog(
              `\n[📋 PREVIEW READY] Please review or edit the lyrics above, then click "✅ Step 2: Confirm Lyrics & Align JSON".`
            );
          } else if (data.returncode === 0) {
            setStage('step2');
            appendLog(`\n[📋] Audio & ASR ready. Paste or review lyrics above, then click Confirm.`);
          } else {
            appendLog(`\n[!] Step 1 exited with code ${data.returncode}.`);
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

  // STEP 2: Confirm Previewed Lyrics & Run Step 4 Alignment to generate <vid>.json
  const handleConfirmAndAlign = async () => {
    const targetUrl = resolvedVid() || url().trim();
    if (!targetUrl) return;

    setIsRunning(true);
    setActiveStep(4);
    appendLog(`\n[*] Step 2: Confirming previewed lyrics & running forced alignment for ${targetUrl}...`);

    let lyricsFile: string | undefined = undefined;
    const finalLyricsText = editableLyrics().trim();

    if (finalLyricsText && !pureAsrOnly()) {
      try {
        appendLog(`[*] Locking in confirmed lyrics (${finalLyricsText.split('\n').length} lines)...`);
        const prep = await prepareLyrics(finalLyricsText, targetUrl);
        if (prep.ok) lyricsFile = prep.lyrics_file;
      } catch (e: any) {
        appendLog(`[!] Failed to stage confirmed lyrics: ${e.message}`);
        setIsRunning(false);
        return;
      }
    }

    cancelSSE = subscribePipelineSSE(
      {
        url: targetUrl,
        query: customQuery().trim() || undefined,
        target_lang: targetLang() !== 'auto' ? targetLang() : undefined,
        asr_engine: asrEngine(),
        lyrics_file: lyricsFile,
        skip_demucs: skipDemucs(),
        skip_netease: pureAsrOnly() || Boolean(lyricsFile),
        force_lrc: false,
        force_asr: false,
        preview_only: false,
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
            appendLog(`\n[✅] Alignment complete! Loading ${data.video_id}.json into Workbench...`);
            await store.loadProjectList();
            await store.selectProject(data.video_id);
            setTimeout(() => store.setShowIngestModal(false), 600);
          } else {
            appendLog(`\n[!] Alignment exited with code ${data.returncode}.`);
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
        <div class="modal-card modal-xlarge" onClick={(e) => e.stopPropagation()}>
          <div class="modal-header">
            <h3>🚀 Song Import & Pre-Alignment Lyrics Preview</h3>
            <button class="btn-close" onClick={handleClose}>✕</button>
          </div>

          <div class="modal-body">
            {/* Top Bar: Video ID, Target Language & ASR Engine */}
            <div style={{ display: 'grid', 'grid-template-columns': '2fr 1fr 1fr', gap: '10px' }}>
              <label class="form-label">
                YouTube Video Code (11-char ID) or URL:
                <input
                  class="form-input"
                  placeholder="e.g. jgWBRVy3uIg or https://www.youtube.com/watch?v=..."
                  value={url()}
                  onInput={(e) => handleUrlChange(e.currentTarget.value)}
                  disabled={isRunning()}
                />
              </label>
              <label class="form-label">
                Target Language:
                <select
                  class="form-input"
                  value={targetLang()}
                  onChange={(e) => setTargetLang(e.currentTarget.value)}
                  disabled={isRunning()}
                >
                  <option value="auto">Auto-Detect (ASR + Script)</option>
                  <option value="zh">Mandarin (Hanzi / Pinyin)</option>
                  <option value="en">English (Pure Latin)</option>
                  <option value="ja">Japanese (Kana / Romaji)</option>
                  <option value="ko">Korean (Hangul / Romaja)</option>
                </select>
              </label>
              <label class="form-label">
                ASR Engine:
                <select
                  class="form-input"
                  value={asrEngine()}
                  onChange={(e) => setAsrEngine(e.currentTarget.value)}
                  disabled={isRunning()}
                >
                  <option value="auto">Auto (Smart Routing)</option>
                  <option value="paraformer">FunASR Paraformer (ZH)</option>
                  <option value="whisper">Faster-Whisper (EN/JA/KO)</option>
                  <option value="sensevoice">SenseVoiceSmall (Fast)</option>
                </select>
              </label>
            </div>

            {/* 2-Step Stage Switcher */}
            <div class="tab-nav">
              <button
                class={`tab-btn ${stage() === 'step1' ? 'active' : ''}`}
                onClick={() => setStage('step1')}
                disabled={isRunning()}
              >
                1️⃣ Step 1: Audio, ASR & Lyrics Search
              </button>
              <button
                class={`tab-btn ${stage() === 'step2' ? 'active' : ''}`}
                onClick={() => setStage('step2')}
                disabled={isRunning()}
              >
                2️⃣ Step 2: Preview / Edit Lyrics & Align ({candidates().length} sources)
              </button>
            </div>

            {/* ================= STAGE 1: CONFIGURE & DISCOVER ================= */}
            <Show when={stage() === 'step1'}>
              <div style={{ display: 'flex', gap: '8px', 'align-items': 'center' }}>
                <input
                  class="form-input"
                  style={{ flex: 1 }}
                  placeholder="Optional custom search title/artist (leave blank to auto-detect from YouTube & ASR)..."
                  value={customQuery()}
                  onInput={(e) => setCustomQuery(e.currentTarget.value)}
                  disabled={isRunning() || isSearching()}
                />
                <button
                  class="btn btn-secondary"
                  onClick={handleQuickSearchLyrics}
                  disabled={isRunning() || isSearching() || (!url().trim() && !customQuery().trim())}
                  title="Search lyrics databases immediately and jump to the Lyrics Preview"
                >
                  {isSearching() ? 'Searching...' : '🔍 Quick Search Lyrics'}
                </button>
                <button
                  class="btn btn-subtle"
                  onClick={() => setStage('step2')}
                  disabled={isRunning()}
                  title="Paste your own lyrics directly before running alignment"
                >
                  📝 Paste Custom Lyrics ➔
                </button>
              </div>

              <div class="checkbox-row" style={{ 'margin-top': '4px' }}>
                <label class="form-checkbox">
                  <input
                    type="checkbox"
                    checked={skipDemucs()}
                    onChange={(e) => setSkipDemucs(e.currentTarget.checked)}
                    disabled={isRunning()}
                  />
                  Skip Demucs Vocal Split (Faster if vocals already separated)
                </label>
                <label class="form-checkbox">
                  <input
                    type="checkbox"
                    checked={forceLrc()}
                    onChange={(e) => setForceLrc(e.currentTarget.checked)}
                    disabled={isRunning()}
                  />
                  Force Re-fetch Lyrics
                </label>
                <label class="form-checkbox">
                  <input
                    type="checkbox"
                    checked={forceAsr()}
                    onChange={(e) => setForceAsr(e.currentTarget.checked)}
                    disabled={isRunning()}
                  />
                  Force Re-run ASR
                </label>
                <label class="form-checkbox">
                  <input
                    type="checkbox"
                    checked={pureAsrOnly()}
                    onChange={(e) => setPureAsrOnly(e.currentTarget.checked)}
                    disabled={isRunning()}
                  />
                  🎙️ Pure ASR Only (Skip external lyrics databases)
                </label>
              </div>
            </Show>

            {/* ================= STAGE 2: LYRICS PREVIEW & EDITOR ================= */}
            <Show when={stage() === 'step2'}>
              <Show when={songTitle()}>
                <div
                  style={{
                    background: 'rgba(88, 166, 255, 0.1)',
                    border: '1px solid rgba(88, 166, 255, 0.3)',
                    'border-radius': '6px',
                    padding: '6px 12px',
                    'font-size': '12px',
                    display: 'flex',
                    'justify-content': 'space-between',
                    'align-items': 'center',
                  }}
                >
                  <span>
                    🎵 <strong>Detected Song:</strong> {songTitle()}{' '}
                    <span style={{ color: 'var(--text-muted)' }}>({resolvedVid() || url()})</span>
                  </span>
                  <button
                    class="btn-subtle"
                    style={{ padding: '2px 8px', 'font-size': '11px' }}
                    onClick={handleQuickSearchLyrics}
                    disabled={isSearching() || isRunning()}
                  >
                    🔄 Re-search Databases
                  </button>
                </div>
              </Show>

              {/* Candidate Selector Cards */}
              <Show when={candidates().length > 0}>
                <div style={{ 'font-size': '12px', color: 'var(--text-muted)', 'margin-bottom': '2px' }}>
                  Select a discovered lyrics source below to load it into the <strong>Lyrics Preview Editor</strong>:
                </div>
                <div class="candidate-list" style={{ 'max-height': '165px' }}>
                  <For each={candidates()}>
                    {(cand) => {
                      const isSelected = () => selectedCandId() === cand.id;
                      const badgeClass =
                        cand.source === 'netease' || cand.source === 'netease_cached'
                          ? 'badge-netease'
                          : cand.source === 'lrclib'
                          ? 'badge-lrclib'
                          : 'badge-yt';

                      return (
                        <div
                          class={`candidate-card ${isSelected() ? 'selected' : ''} ${
                            !cand.validation.is_valid ? 'invalid' : ''
                          }`}
                          onClick={() => selectCandidateForPreview(cand)}
                        >
                          <div class="cand-top-row">
                            <div class="cand-title">
                              <input
                                type="radio"
                                name="selected_candidate"
                                checked={isSelected()}
                                onChange={() => selectCandidateForPreview(cand)}
                              />
                              <span class={`badge ${badgeClass}`}>{cand.source}</span>
                              <span>
                                {cand.title} {cand.artist ? `— ${cand.artist}` : ''}
                              </span>
                            </div>
                            <div style={{ display: 'flex', gap: '6px', 'align-items': 'center' }}>
                              <span class={`badge ${cand.is_synced ? 'badge-synced' : 'badge-plain'}`}>
                                {cand.is_synced ? '⏱️ Synced LRC' : '📄 Plain Text'} ({cand.line_count} lines)
                              </span>
                              <span
                                class={`badge ${cand.validation.is_valid ? 'badge-valid' : 'badge-warn'}`}
                              >
                                {cand.validation.is_valid
                                  ? `🟢 Verified (${Math.round(cand.validation.score * 100)}%)`
                                  : '⚠️ Rejected / Mismatch'}
                              </span>
                            </div>
                          </div>
                          <div class="cand-meta">
                            <Show when={cand.script}>
                              <span>
                                Script: {cand.script!.dominant.toUpperCase()} (
                                {Math.round(cand.script!.han_ratio * 100)}% Hanzi,{' '}
                                {Math.round(cand.script!.latin_ratio * 100)}% Latin)
                              </span>
                            </Show>
                            <span
                              style={{
                                color: cand.validation.is_valid ? 'var(--text-muted)' : '#ff6b6b',
                              }}
                            >
                              {cand.validation.reason}
                            </span>
                          </div>
                        </div>
                      );
                    }}
                  </For>
                </div>
              </Show>

              {/* Editable Lyrics Preview Box */}
              <div style={{ display: 'flex', 'flex-direction': 'column', gap: '6px', 'margin-top': '4px' }}>
                <div style={{ display: 'flex', 'justify-content': 'space-between', 'align-items': 'center' }}>
                  <span style={{ 'font-size': '12px', 'font-weight': 600, color: 'var(--text-main)' }}>
                    📋 Lyrics Preview & Editor (Edit or paste lyrics before running Step 4 Alignment):
                  </span>
                  <Show when={liveValidation()}>
                    <div style={{ display: 'flex', gap: '8px', 'font-size': '11px', 'align-items': 'center' }}>
                      <span
                        class={`badge ${
                          liveValidation().validation.is_valid ? 'badge-valid' : 'badge-warn'
                        }`}
                      >
                        {liveValidation().validation.is_valid ? '🟢 Sense-Check Passed' : '⚠️ Sense-Check Warning'}
                      </span>
                      <span style={{ color: 'var(--text-muted)' }}>
                        {liveValidation().is_synced ? '⏱️ Synced LRC' : '📄 Plain Lyrics'} •{' '}
                        {liveValidation().line_count} lines •{' '}
                        {Math.round(liveValidation().script.han_ratio * 100)}% Hanzi /{' '}
                        {Math.round(liveValidation().script.latin_ratio * 100)}% Latin
                      </span>
                    </div>
                  </Show>
                </div>

                <textarea
                  class="form-textarea"
                  style={{
                    height: '180px',
                    'font-family': 'var(--font-mono)',
                    'font-size': '12px',
                    'line-height': '1.45',
                  }}
                  placeholder="Lyrics to align will appear here after Step 1, or you can paste timestamped LRC [00:12.34] or plain lyrics directly..."
                  value={editableLyrics()}
                  onInput={(e) => handleValidatePreviewText(e.currentTarget.value)}
                  disabled={isRunning()}
                />
              </div>
            </Show>

            {/* Pipeline progress steps */}
            <div class="pipeline-steps">
              <div class={stepClass(1)}>1. Audio Download</div>
              <div class={stepClass(2)}>2. Demucs Vocals</div>
              <div class={stepClass(3)}>3. ASR & Lyrics Sense-Check</div>
              <div class={stepClass(4)}>4. Confirm & Align JSON</div>
            </div>

            {/* Terminal Log */}
            <div ref={logContainerRef} class="terminal-log" style={{ height: '125px' }}>
              {logs().map((l) => (
                <div class="log-line">{l}</div>
              ))}
            </div>
          </div>

          <div class="modal-footer" style={{ display: 'flex', 'justify-content': 'space-between', width: '100%' }}>
            <div style={{ display: 'flex', gap: '8px' }}>
              <button class="btn" onClick={handleClose} disabled={isRunning()}>
                Close
              </button>
              <Show when={stage() === 'step2'}>
                <button
                  class="btn btn-secondary"
                  onClick={() => setStage('step1')}
                  disabled={isRunning()}
                >
                  ◀ Back to Step 1 (Re-fetch)
                </button>
              </Show>
            </div>

            <div style={{ display: 'flex', gap: '8px' }}>
              <Show when={stage() === 'step1'}>
                <button
                  class="btn btn-lead"
                  onClick={handleRunStep1Preview}
                  disabled={isRunning() || !url().trim()}
                >
                  {isRunning()
                    ? '⏳ Fetching Audio & Discovering Lyrics...'
                    : '🔍 Step 1: Fetch Audio & Preview Lyrics'}
                </button>
              </Show>

              <Show when={stage() === 'step2'}>
                <button
                  class="btn btn-lead"
                  onClick={handleConfirmAndAlign}
                  disabled={isRunning() || !url().trim() || (!editableLyrics().trim() && !pureAsrOnly())}
                >
                  {isRunning()
                    ? '⏳ Aligning Lyrics & Creating JSON...'
                    : '✅ Step 2: Confirm Lyrics & Create JSON'}
                </button>
              </Show>
            </div>
          </div>
        </div>
      </div>
    </Show>
  );
};
