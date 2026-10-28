import { Component, Show, createEffect, createSignal, onCleanup, onMount } from 'solid-js';
import WaveSurfer from 'wavesurfer.js';
// @ts-ignore
import RegionsPlugin from 'wavesurfer.js/dist/plugins/regions.esm.js';
import { store, fmtMs } from '../state/store';

export const WaveformPane: Component<{
  onRegisterControls: (controls: {
    seekToMs: (ms: number) => void;
    playBounds: (startMs: number, endMs?: number) => void;
  }) => void;
}> = (props) => {
  let containerRef!: HTMLDivElement;
  let wrapperRef!: HTMLDivElement;
  let selectionOverlayRef!: HTMLDivElement;
  let ws: WaveSurfer | null = null;
  let wsRegions: any = null;
  let isWaveReady = false;
  let stopAtSec: number | null = null;
  const regionMap = new Map<string, { lineIdx: number; tokenIdx: number }>();

  const [waveHeight, setWaveHeight] = createSignal<number>(100);

  const seekToMs = (ms: number) => {
    if (!ws || !isWaveReady) return;
    stopAtSec = null;
    ws.setTime(Math.max(0, ms / 1000.0));
  };

  const playBounds = (startMs: number, endMs?: number) => {
    if (!ws || !isWaveReady) return;
    stopAtSec = endMs !== undefined ? endMs / 1000.0 : null;
    ws.setTime(Math.max(0, startMs / 1000.0));
    ws.play();
  };

  const playActiveLine = () => {
    const cur = store.lines()[store.activeLineIdx()];
    if (!cur || !ws || !isWaveReady) return;
    const activeToks = cur.tokens.filter((t) => !t.is_deleted);
    const startMs = activeToks.length ? activeToks[0].start : cur.start;
    const endMs = activeToks.length ? activeToks[activeToks.length - 1].end : cur.end;
    playBounds(startMs, endMs + 150);
  };

  const seekRelative = (deltaSec: number) => {
    if (!ws || !isWaveReady) return;
    stopAtSec = null;
    const next = Math.max(0, Math.min(ws.getDuration(), ws.getCurrentTime() + deltaSec));
    ws.setTime(next);
  };

  onMount(() => {
    const savedH = localStorage.getItem('singalong_waveform_height');
    if (savedH) {
      const h = parseInt(savedH, 10);
      if (!isNaN(h) && h >= 65 && h <= 300) setWaveHeight(h);
    }

    props.onRegisterControls({ seekToMs, playBounds });

    wsRegions = RegionsPlugin.create();

    ws = WaveSurfer.create({
      container: containerRef,
      waveColor: '#4f46e5',
      progressColor: '#FFD700',
      cursorColor: '#ffffff',
      cursorWidth: 2,
      height: waveHeight(),
      normalize: true,
      minPxPerSec: store.waveformZoom(),
      autoScroll: true,
      autoCenter: true,
      plugins: [wsRegions],
    });

    ws.on('ready', () => {
      isWaveReady = true;
      store.setTotalDurationMs(Math.round((ws?.getDuration() || 0) * 1000));
      try {
        ws?.zoom(store.waveformZoom());
      } catch {}
      syncRegions();
    });

    ws.on('timeupdate', (curSec: number) => {
      const curMs = Math.round(curSec * 1000);
      if (stopAtSec !== null && curSec >= stopAtSec) {
        stopAtSec = null;
        ws?.pause();
        return;
      }
      store.updateActiveLineFromTime(curMs);
    });

    ws.on('play', () => store.setIsPlaying(true));
    ws.on('pause', () => {
      store.setIsPlaying(false);
      stopAtSec = null;
    });

    wsRegions.on('region-updated', (region: any) => {
      const meta = regionMap.get(region.id);
      if (meta) {
        store.updateTokenTimes(meta.lineIdx, meta.tokenIdx, region.start * 1000, region.end * 1000);
      }
    });

    wsRegions.on('region-clicked', (region: any, e: MouseEvent) => {
      e.stopPropagation();
      const meta = regionMap.get(region.id);
      if (!meta) return;
      if (e.altKey) {
        store.toggleTokenDeleted(meta.lineIdx, meta.tokenIdx);
        return;
      }
      if (e.shiftKey) {
        store.shiftSelectToken(meta.tokenIdx);
        return;
      }
      if (e.ctrlKey || e.metaKey) {
        store.toggleSelectToken(meta.tokenIdx);
        return;
      }
      store.selectSingleToken(meta.tokenIdx);
      seekToMs(region.start * 1000);
    });

    // Shift+Drag rectangular selection on waveform
    let isShiftDragging = false;
    let dragStartX = 0;

    const onMouseDown = (e: MouseEvent) => {
      if (e.shiftKey && e.button === 0) {
        isShiftDragging = true;
        dragStartX = e.clientX;
        const rect = wrapperRef.getBoundingClientRect();
        selectionOverlayRef.style.left = `${dragStartX - rect.left}px`;
        selectionOverlayRef.style.width = '0px';
        selectionOverlayRef.style.display = 'block';
      }
    };

    const onMouseMove = (e: MouseEvent) => {
      if (!isShiftDragging) return;
      const rect = wrapperRef.getBoundingClientRect();
      const curX = Math.max(rect.left, Math.min(rect.right, e.clientX));
      const left = Math.min(dragStartX, curX) - rect.left;
      const width = Math.abs(curX - dragStartX);
      selectionOverlayRef.style.left = `${left}px`;
      selectionOverlayRef.style.width = `${width}px`;
    };

    const onMouseUp = (e: MouseEvent) => {
      if (!isShiftDragging) return;
      isShiftDragging = false;
      selectionOverlayRef.style.display = 'none';
      const minX = Math.min(dragStartX, e.clientX);
      const maxX = Math.max(dragStartX, e.clientX);
      if (maxX - minX < 5 || !wsRegions) return;

      const hitIndices: number[] = [];
      wsRegions.getRegions().forEach((reg: any) => {
        if (reg.element) {
          const r = reg.element.getBoundingClientRect();
          if (r.right >= minX && r.left <= maxX) {
            const meta = regionMap.get(reg.id);
            if (meta) hitIndices.push(meta.tokenIdx);
          }
        }
      });
      if (hitIndices.length > 0) {
        store.selectTokenRange(hitIndices);
      }
    };

    wrapperRef.addEventListener('mousedown', onMouseDown);
    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);

    onCleanup(() => {
      wrapperRef.removeEventListener('mousedown', onMouseDown);
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
      if (ws) ws.destroy();
    });
  });

  // Load audio when currentVid or stemMode changes
  createEffect(() => {
    const vid = store.currentVid();
    const mode = store.stemMode();
    if (!ws || !vid) return;

    isWaveReady = false;
    const audioUrl = `/api/audio/${encodeURIComponent(vid)}/${mode}`;
    const prevTime = ws.getCurrentTime() || 0;
    const wasPlaying = ws.isPlaying();

    ws.load(audioUrl).then(() => {
      if (prevTime > 0 && prevTime < ws!.getDuration()) {
        ws!.setTime(prevTime);
        if (wasPlaying) ws!.play();
      } else {
        const firstLn = store.lines()[store.activeLineIdx()];
        if (firstLn) {
          ws!.setTime(Math.max(0, (firstLn.start - 300) / 1000.0));
        }
      }
    });
  });

  const syncRegions = () => {
    if (!wsRegions || !isWaveReady) return;
    wsRegions.clearRegions();
    regionMap.clear();

    const idx = store.activeLineIdx();
    const lns = store.lines();
    const curLine = lns[idx];
    if (!curLine) return;

    const selectedSet = new Set(store.selectedTokenIndices());
    const isBacking = !!curLine.is_backing;

    curLine.tokens.forEach((t, tIdx) => {
      const sSec = t.start / 1000.0;
      const eSec = Math.max(sSec + 0.03, t.end / 1000.0);
      const isSelected = selectedSet.has(tIdx);

      let color = isBacking ? 'rgba(112, 214, 255, 0.28)' : 'rgba(255, 215, 0, 0.26)';
      if (t.is_deleted) color = 'rgba(148, 163, 184, 0.12)';
      if (isSelected) color = 'rgba(99, 102, 241, 0.48)';

      let label = t.is_deleted ? `✕${t.text}` : t.text;
      if (t.comment) label = `💬${label}`;

      const regId = `tok-${idx}-${tIdx}`;
      wsRegions.addRegion({
        id: regId,
        start: sSec,
        end: eSec,
        content: label,
        color,
        drag: true,
        resize: true,
      });
      regionMap.set(regId, { lineIdx: idx, tokenIdx: tIdx });
    });
  };

  createEffect(() => {
    // Subscribe to activeLineIdx, lines, and selectedTokenIndices
    store.activeLineIdx();
    store.lines();
    store.selectedTokenIndices();
    syncRegions();
  });

  // Start / Cancel / Step Tap-Along
  const toggleTapAlong = () => {
    if (store.isTapMode()) {
      store.cancelTapAlongSession();
      if (ws) {
        ws.setPlaybackRate(1.0);
        store.setPlaybackRate(1.0);
        ws.pause();
      }
      return;
    }
    const curLine = store.lines()[store.activeLineIdx()];
    if (!curLine || !ws || !isWaveReady) return;
    const activeToks = curLine.tokens.filter((t) => !t.is_deleted);
    if (!activeToks.length) return;

    if (store.startTapAlongSession()) {
      stopAtSec = null;
      const playStart = Math.max(0, (activeToks[0].start - 2000) / 1000.0);
      ws.setPlaybackRate(0.75);
      store.setPlaybackRate(0.75);
      ws.setTime(playStart);
      ws.play();
    }
  };

  const handleTapSpace = () => {
    if (!store.isTapMode() || !ws) return;
    const curMs = Math.round(ws.getCurrentTime() * 1000);
    const lns = JSON.parse(JSON.stringify(store.lines()));
    const lineIdx = store.activeLineIdx();
    const curLine = lns[lineIdx];
    if (!curLine) return;

    const activeToks = curLine.tokens.filter((t: any) => !t.is_deleted);
    const tapIdx = store.tapTokenIdx();
    if (tapIdx >= activeToks.length) return;

    activeToks[tapIdx].start = curMs;
    if (tapIdx > 0) {
      activeToks[tapIdx - 1].end = Math.max(activeToks[tapIdx - 1].start + 40, curMs);
    }

    const nextTapIdx = tapIdx + 1;
    if (nextTapIdx >= activeToks.length) {
      activeToks[activeToks.length - 1].end = curMs + 600;
      curLine.start = activeToks[0].start;
      curLine.end = activeToks[activeToks.length - 1].end;
      store.setLines(lns);
      store.finishTapAlongSession();
      ws.setPlaybackRate(1.0);
      store.setPlaybackRate(1.0);
      ws.pause();
    } else {
      store.setTapTokenIdx(nextTapIdx);
      store.setLines(lns);
    }
  };

  const startResizeWave = (e: MouseEvent) => {
    e.preventDefault();
    const startY = e.clientY;
    const startH = waveHeight();

    const onMove = (ev: MouseEvent) => {
      const nextH = Math.max(65, Math.min(280, startH + (ev.clientY - startY)));
      setWaveHeight(nextH);
      if (ws) {
        try {
          ws.setOptions({ height: nextH });
        } catch {}
      }
    };
    const onUp = () => {
      localStorage.setItem('singalong_waveform_height', String(waveHeight()));
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };

  // Global keyboard shortcuts
  onMount(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement ||
        e.target instanceof HTMLSelectElement
      ) {
        return;
      }

      if (store.isTapMode()) {
        if (e.code === 'Space') {
          e.preventDefault();
          handleTapSpace();
          return;
        } else if (e.key === 'Escape') {
          e.preventDefault();
          toggleTapAlong();
          return;
        }
      }

      if (e.key === 'c' || e.key === 'C') {
        e.preventDefault();
        store.openCommentModal();
      } else if (e.code === 'Space') {
        e.preventDefault();
        ws?.playPause();
      } else if (e.key === 'r' || e.key === 'R') {
        e.preventDefault();
        playActiveLine();
      } else if (e.key === 'j' || e.key === 'J') {
        e.preventDefault();
        seekRelative(-3);
      } else if (e.key === 'l' || e.key === 'L') {
        e.preventDefault();
        seekRelative(3);
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        if (store.activeLineIdx() > 0) {
          const nextIdx = store.activeLineIdx() - 1;
          store.setActiveLineIdx(nextIdx);
          const ln = store.lines()[nextIdx];
          if (ln) seekToMs(ln.start - 200);
        }
      } else if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (store.activeLineIdx() < store.lines().length - 1) {
          const nextIdx = store.activeLineIdx() + 1;
          store.setActiveLineIdx(nextIdx);
          const ln = store.lines()[nextIdx];
          if (ln) seekToMs(ln.start - 200);
        }
      } else if (e.key === 'v' || e.key === 'V') {
        e.preventDefault();
        store.setStemMode(store.stemMode() === 'vocals' ? 'mix' : 'vocals');
      } else if (e.key === 'b' || e.key === 'B') {
        e.preventDefault();
        store.toggleLineBacking(store.activeLineIdx());
      } else if (e.key === 't' || e.key === 'T') {
        e.preventDefault();
        toggleTapAlong();
      } else if ((e.ctrlKey || e.metaKey) && (e.key === 's' || e.key === 'S')) {
        e.preventDefault();
        store.saveCurrentProject();
      } else if ((e.ctrlKey || e.metaKey) && (e.key === 'z' || e.key === 'Z')) {
        e.preventDefault();
        store.undo();
      } else if ((e.ctrlKey || e.metaKey) && (e.key === 'a' || e.key === 'A')) {
        e.preventDefault();
        store.selectAllTokensInLine(true);
      } else if (e.key === 'Escape') {
        if (store.showCommentModal()) {
          store.setShowCommentModal(false);
        } else if (store.editingToken()) {
          store.setEditingToken(null);
        } else if (store.selectedTokenIndices().length > 0) {
          store.clearTokenSelection();
        }
      }
    };
    window.addEventListener('keydown', onKeyDown);
    onCleanup(() => window.removeEventListener('keydown', onKeyDown));
  });

  const canSplitSelection = () => {
    const sel = store.selectedTokenIndices();
    return sel.length > 0 && sel[0] > 0;
  };

  return (
    <>
      <section class="waveform-container">
        <div class="waveform-toolbar">
          <div class="waveform-controls">
            <button class="btn btn-primary" onClick={() => ws?.playPause()} title="Play / Pause continuous audio (Spacebar)">
              {store.isPlaying() ? '⏸ Pause (Space)' : '▶ Play (Space)'}
            </button>
            <button class="btn" onClick={playActiveLine} title="Play only the active line and stop at its end (R)">
              🎯 Play Line (R)
            </button>
            <button
              class="btn"
              onClick={() => {
                if (store.activeLineIdx() > 0) {
                  const next = store.activeLineIdx() - 1;
                  store.setActiveLineIdx(next);
                  const ln = store.lines()[next];
                  if (ln) seekToMs(ln.start - 200);
                }
              }}
              title="Previous Line (↑)"
            >
              ◀ Prev Line
            </button>
            <button
              class="btn"
              onClick={() => {
                if (store.activeLineIdx() < store.lines().length - 1) {
                  const next = store.activeLineIdx() + 1;
                  store.setActiveLineIdx(next);
                  const ln = store.lines()[next];
                  if (ln) seekToMs(ln.start - 200);
                }
              }}
              title="Next Line (↓)"
            >
              Next Line ▶
            </button>
            <button class="btn" onClick={() => seekRelative(-3)} title="Rewind 3s (J)">
              ⏪ -3s (J)
            </button>
            <button class="btn" onClick={() => seekRelative(3)} title="Forward 3s (L)">
              +3s ⏩ (L)
            </button>
            <button
              class="btn btn-lead"
              onClick={() => store.openCommentModal()}
              title="Add / Edit Analysis Comment on active or selected syllables (C)"
            >
              💬 Comment (C)
            </button>
            <button
              class="btn"
              style={store.isTapMode() ? { background: '#ef4444', color: '#fff', 'border-color': '#ef4444' } : { 'border-color': '#f87171', color: '#f87171' }}
              onClick={toggleTapAlong}
              title="Rhythm Retiming Mode at 0.75x speed (T)"
            >
              {store.isTapMode() ? '🔴 Tapping... (Esc)' : '🥁 Tap-Along (T)'}
            </button>
            <button
              class={`btn ${store.followPlayhead() ? 'active' : ''}`}
              style={store.followPlayhead() ? { 'border-color': 'var(--accent-primary)' } : {}}
              onClick={() => store.setFollowPlayhead(!store.followPlayhead())}
              title="Automatically switch active line as song plays"
            >
              📍 Follow: {store.followPlayhead() ? 'ON' : 'OFF'}
            </button>
          </div>

          <div class="time-readout">
            <span class="current">{fmtMs(store.currentTimeMs())}</span> / <span>{fmtMs(store.totalDurationMs())}</span>
          </div>

          <div class="kb-hints">
            <span>Speed:</span>
            <select
              class="speed-select"
              value={String(store.playbackRate())}
              onChange={(e) => {
                const rate = parseFloat(e.currentTarget.value);
                store.setPlaybackRate(rate);
                ws?.setPlaybackRate(rate);
              }}
            >
              <option value="0.5">0.5x</option>
              <option value="0.75">0.75x</option>
              <option value="1">1.0x</option>
              <option value="1.25">1.25x</option>
            </select>
            <span>Zoom:</span>
            <input
              type="range"
              min="15"
              max="300"
              value={store.waveformZoom()}
              onInput={(e) => {
                const z = parseInt(e.currentTarget.value, 10);
                store.setWaveformZoom(z);
                try {
                  ws?.zoom(z);
                } catch {}
              }}
              style={{ width: '85px' }}
            />
          </div>
        </div>

        {/* Second row: Line & Syllable Operations Bar */}
        <div class="line-ops-bar">
          <div class="line-ops-left">
            <span class="active-line-label">
              Active: <strong>Line #{store.activeLineIdx() + 1}</strong>
            </span>
            <button class="btn btn-sm" onClick={() => store.nudgeActiveLine(-50)} title="Shift entire active line earlier by 50ms">
              ◀ -50ms
            </button>
            <button class="btn btn-sm" onClick={() => store.nudgeActiveLine(50)} title="Shift entire active line later by 50ms">
              +50ms ▶
            </button>
            <button class="btn btn-sm" onClick={() => store.realignLineUniform()} title="Evenly distribute syllable durations across line window">
              ⚖ Even Syllables
            </button>
            <button class="btn btn-sm" onClick={() => store.toggleLineBacking(store.activeLineIdx())} title="Toggle Lead / Backing track (B)">
              👥 Toggle Backing (B)
            </button>
            <button
              class="btn btn-sm"
              disabled={store.activeLineIdx() >= store.lines().length - 1}
              onClick={() => store.mergeWithNextLine(store.activeLineIdx())}
              title="Merge the next line into the end of this line"
            >
              🔗 Merge Next Line
            </button>
          </div>

          <Show
            when={store.selectedTokenIndices().length > 0}
            fallback={
              <div class="selection-hint">
                💡 Select any syllable(s) (Click, Shift+Click, or Shift+Drag on waveform) to Split Line or Push to Prev/Next Line
              </div>
            }
          >
            <div class="selection-action-pill">
              <span class="sel-count">{store.selectedTokenIndices().length} syllable(s) selected:</span>
              <button
                class="btn btn-sm"
                disabled={store.activeLineIdx() === 0}
                onClick={() => store.pushSyllableToPrevLine(store.activeLineIdx())}
                title="Move selected syllable(s) into previous line"
              >
                ⬆ Push to Prev Line
              </button>
              <button
                class="btn btn-sm"
                disabled={!canSplitSelection()}
                onClick={() => store.splitLineAtToken(store.activeLineIdx(), store.selectedTokenIndices()[0])}
                title="Split active line starting at the first selected syllable"
              >
                ✂ Split to New Line
              </button>
              <button
                class="btn btn-sm"
                disabled={store.activeLineIdx() >= store.lines().length - 1}
                onClick={() => store.pushSyllableToNextLine(store.activeLineIdx())}
                title="Move selected syllable(s) into next line"
              >
                ⬇ Push to Next Line
              </button>
              <button
                class="btn btn-sm btn-lead"
                onClick={() => store.openCommentModal()}
                title="Add / Edit Comment on selected syllable(s) (C)"
              >
                💬 Comment (C)
              </button>
              <button
                class="btn btn-sm"
                onClick={() => store.toggleTokenDeleted(store.activeLineIdx(), store.selectedTokenIndices()[0])}
                title="Toggle Delete / Restore on selected syllable(s)"
              >
                ❌ Delete / Restore
              </button>
              <button class="btn btn-sm" onClick={() => store.clearTokenSelection()} title="Clear selection (Esc)">
                ✕
              </button>
            </div>
          </Show>
        </div>

        <div ref={wrapperRef} class="waveform-canvas-wrap">
          <div ref={selectionOverlayRef} class="waveform-selection-overlay" />
          <div ref={containerRef} class="waveform-canvas" />
        </div>
      </section>
      <div class="resizer-gutter" onMouseDown={startResizeWave} title="Drag to resize Waveform height" />
    </>
  );
};
