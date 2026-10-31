import { Component, For, Show, createEffect, createMemo, createSignal, onCleanup, onMount } from 'solid-js';
import { store, fmtMs } from '../state/store';
import { Line, Token } from '../types';

interface RenderLineWindow {
  line: Line;
  lineIdx: number;
  dispStart: number;
  dispEnd: number;
  isBacking: boolean;
  isForeign: boolean;
  columns: {
    token: Token;
    tokenIdx: number;
    width: number;
    gapAfter: number;
    hzTxt: string;
    pyTxt: string;
    enTxt: string;
  }[];
  scaleRatio: number;
  scaledTotalWidth: number;
}

function estimateTextWidth(text: string, fontSize: number, bold: boolean = false): number {
  if (!text) return 0.0;
  let w = 0.0;
  for (const ch of text) {
    const cp = ch.charCodeAt(0);
    if ((cp >= 0x3000 && cp <= 0x9fff) || (cp >= 0xac00 && cp <= 0xd7a3) || (cp >= 0xff00 && cp <= 0xffef)) {
      w += 1.0 * fontSize;
    } else if ("iljtfI1'.,:;!|()".includes(ch)) {
      w += 0.36 * fontSize;
    } else if ('mwMW'.includes(ch)) {
      w += 0.84 * fontSize;
    } else if (ch >= 'A' && ch <= 'Z') {
      w += 0.68 * fontSize;
    } else if (ch === ' ') {
      w += 0.33 * fontSize;
    } else {
      w += 0.56 * fontSize;
    }
  }
  if (bold) w *= 1.05;
  w += text.length * 1.2 + 4.0;
  return w;
}

export const DetachedStageWindow: Component = () => {
  let viewportWrapRef!: HTMLDivElement;
  const [videoEl, setVideoEl] = createSignal<HTMLVideoElement | null>(null);
  const [audioEl, setAudioEl] = createSignal<HTMLAudioElement | null>(null);
  const [canvasScale, setCanvasScale] = createSignal<number>(0.65);
  const [localAudioActive, setLocalAudioActive] = createSignal<boolean>(false);
  const [showHud, setShowHud] = createSignal<boolean>(true);
  const [showSafeGuides, setShowSafeGuides] = createSignal<boolean>(false);

  const meta = () => store.projectData()?.metadata;
  const hasBgMedia = () => Boolean(meta()?.bg_media_path || meta()?.bg_video_path);
  const bgStreamUrl = () =>
    meta()?.bg_stream_url ||
    (meta()?.bg_media_path
      ? `/api/bg-media/stream?path=${encodeURIComponent(meta()?.bg_media_path!.split('/').pop() || '')}`
      : '');
  const bgMediaType = () =>
    meta()?.bg_media_type || (meta()?.bg_media_path?.match(/\.(jpe?g|png|webp|bmp)$/i) ? 'image' : 'video');
  const bgOffsetMs = () => meta()?.bg_offset_ms || 0;
  const bgDimPct = () => meta()?.bg_dim_pct ?? 35;

  const computeTargetSec = (audioTimeMs: number, offsetMs: number, dur?: number) => {
    const rawSec = Math.max(0, (audioTimeMs + offsetMs) / 1000.0);
    if (dur && isFinite(dur) && dur > 0.5) {
      return rawSec % dur;
    }
    return rawSec;
  };

  // Auto-scale 1920x1080 virtual canvas to fit window with 16:9 letterbox/pillarbox
  const updateCanvasScale = () => {
    if (!viewportWrapRef) return;
    const rect = viewportWrapRef.getBoundingClientRect();
    const scaleX = rect.width / 1920;
    const scaleY = rect.height / 1080;
    setCanvasScale(Math.max(0.15, Math.min(scaleX, scaleY)));
  };

  onMount(() => {
    document.title = `📺 Render Simulator — ${store.currentVid() || 'Singalong Studio'}`;
    updateCanvasScale();
    const ro = new ResizeObserver(() => updateCanvasScale());
    if (viewportWrapRef) ro.observe(viewportWrapRef);
    window.addEventListener('resize', updateCanvasScale);

    // Request initial sync from main editor window
    store.sendStageCommand({ type: 'request_sync' });

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement) return;
      if (e.code === 'Space') {
        e.preventDefault();
        handleTogglePlay();
      } else if (e.key === 'j' || e.key === 'J' || e.key === 'ArrowLeft') {
        e.preventDefault();
        handleSeekRel(-3);
      } else if (e.key === 'l' || e.key === 'L' || e.key === 'ArrowRight') {
        e.preventDefault();
        handleSeekRel(3);
      } else if (e.key === 'f' || e.key === 'F') {
        e.preventDefault();
        toggleFullscreen();
      } else if (e.key === 'h' || e.key === 'H') {
        e.preventDefault();
        setShowHud(!showHud());
      } else if (e.key === 'g' || e.key === 'G') {
        e.preventDefault();
        setShowSafeGuides(!showSafeGuides());
      }
    };
    window.addEventListener('keydown', onKeyDown);

    onCleanup(() => {
      ro.disconnect();
      window.removeEventListener('resize', updateCanvasScale);
      window.removeEventListener('keydown', onKeyDown);
    });
  });

  createEffect(() => {
    const title = meta()?.title || store.currentVid();
    if (title) {
      document.title = `📺 ${title} — 1080p Render Simulator`;
    }
  });

  // Sync background video with store playback & time
  createEffect(() => {
    const v = videoEl();
    const mType = bgMediaType();
    const playing = store.isPlaying();
    const rate = store.playbackRate();
    const url = bgStreamUrl();
    if (!v || mType !== 'video' || !url) return;

    v.muted = true;
    v.playbackRate = rate;
    if (playing) {
      const targetSec = computeTargetSec(store.currentTimeMs(), bgOffsetMs(), v.duration);
      if (Math.abs(v.currentTime - targetSec) > 0.35) {
        try {
          v.currentTime = targetSec;
        } catch {}
      }
      v.play().catch(() => {});
    } else {
      v.pause();
    }
  });

  createEffect(() => {
    const v = videoEl();
    const mType = bgMediaType();
    const audioTimeMs = store.currentTimeMs();
    const offset = bgOffsetMs();
    const playing = store.isPlaying();
    if (!v || mType !== 'video') return;

    const targetSec = computeTargetSec(audioTimeMs, offset, v.duration);
    const threshold = playing ? 0.35 : 0.04;
    if (Math.abs(v.currentTime - targetSec) > threshold) {
      try {
        v.currentTime = targetSec;
      } catch {}
    }
  });

  // Optional standalone audio playback when localAudioActive is enabled
  createEffect(() => {
    const a = audioEl();
    if (!a) return;
    if (!localAudioActive()) {
      a.pause();
      return;
    }
    const playing = store.isPlaying();
    a.playbackRate = store.playbackRate();
    const targetSec = store.currentTimeMs() / 1000.0;
    if (Math.abs(a.currentTime - targetSec) > 0.3) {
      try {
        a.currentTime = targetSec;
      } catch {}
    }
    if (playing) {
      a.play().catch(() => {});
    } else {
      a.pause();
    }
  });

  // Precompute ASS-accurate display windows & column layouts matching core/render.py
  const renderWindows = createMemo<RenderLineWindow[]>(() => {
    const allLines = store.lines();
    const tMode = store.tierMode();
    const showPy = tMode === '3' || tMode === '2';
    const showEn = tMode === '3';

    const dispMap = new Map<number, [number, number]>();

    for (const trackIsBacking of [false, true]) {
      const trackEntries = allLines
        .map((ln, idx) => ({ ln, idx }))
        .filter((item) => Boolean(item.ln.is_backing) === trackIsBacking && item.ln.tokens?.length > 0);

      for (let i = 0; i < trackEntries.length; i++) {
        const { ln, idx } = trackEntries[i];
        const toks = ln.tokens.filter((t) => !t.is_deleted);
        if (!toks.length) continue;

        const firstS = toks[0].start;
        const lastE = toks[toks.length - 1].end;

        let dispS = Math.max(0, firstS - 400);
        let dispE = lastE + 400;

        if (i > 0) {
          const prevToks = trackEntries[i - 1].ln.tokens.filter((t) => !t.is_deleted);
          if (prevToks.length) {
            const prevE = prevToks[prevToks.length - 1].end;
            const midpoint = Math.floor((prevE + firstS) / 2);
            dispS = Math.max(dispS, midpoint + 15);
          }
        }

        if (i + 1 < trackEntries.length) {
          const nextToks = trackEntries[i + 1].ln.tokens.filter((t) => !t.is_deleted);
          if (nextToks.length) {
            const nextS = nextToks[0].start;
            const midpoint = Math.floor((lastE + nextS) / 2);
            dispE = Math.min(dispE, midpoint - 15);
          }
        }

        dispMap.set(idx, [dispS, Math.max(dispS + 100, dispE)]);
      }
    }

    const result: RenderLineWindow[] = [];

    allLines.forEach((ln, lineIdx) => {
      const activeToks = (ln.tokens || []).filter((t) => !t.is_deleted);
      if (!activeToks.length) return;

      const isBacking = Boolean(ln.is_backing);
      const [dispStart, dispEnd] = dispMap.get(lineIdx) || [
        Math.max(0, activeToks[0].start - 400),
        activeToks[activeToks.length - 1].end + 400,
      ];

      const isForeign = activeToks.some((t) => /[\u4e00-\u9fff\uac00-\ud7af\u3040-\u30ff]/.test(t.text));
      const prefix = isBacking ? '( ' : '';
      const suffix = isBacking ? ' )' : '';

      const hzFsize = isBacking ? Math.round(64 * 0.85) : 64;
      const pyFsize = isBacking ? Math.round(32 * 0.85) : 32;
      const enFsize = isBacking ? Math.round(28 * 0.85) : 28;

      const cols = activeToks.map((tok, i) => {
        let hzTxt = tok.text;
        const pyTxt = tok.pinyin || hzTxt;
        const enTxt = tok.en || hzTxt;
        if (i === 0 && prefix) hzTxt = prefix + hzTxt;
        if (i === activeToks.length - 1 && suffix) hzTxt = hzTxt + suffix;

        const wH = estimateTextWidth(hzTxt, hzFsize, true);
        const wP = isForeign && showPy ? estimateTextWidth(pyTxt, pyFsize, false) : 0;
        const wE = isForeign && showEn ? estimateTextWidth(enTxt, enFsize, false) : 0;

        const width = Math.max(wH, wP, wE, hzFsize * 0.85);
        const gapAfter = i + 1 < activeToks.length ? (tok.space ? 26.0 : 12.0) : 0.0;

        return {
          token: tok,
          tokenIdx: i,
          width,
          gapAfter,
          hzTxt,
          pyTxt,
          enTxt,
        };
      });

      const totalW = cols.reduce((acc, c) => acc + c.width + c.gapAfter, 0);
      const maxAvailW = isBacking ? 1920 - 40 - 80 - 80 : 1920 - 140;
      const scaleRatio = Math.min(1.0, maxAvailW / Math.max(1.0, totalW));
      const scaledTotalWidth = totalW * scaleRatio;

      result.push({
        line: ln,
        lineIdx,
        dispStart,
        dispEnd,
        isBacking,
        isForeign,
        columns: cols,
        scaleRatio,
        scaledTotalWidth,
      });
    });

    return result;
  });

  // Visible Lead and Backing lines at currentTimeMs (exactly matching ASS subtitle visibility)
  const visibleLeadWindow = createMemo(() => {
    const t = store.currentTimeMs();
    const wins = renderWindows();
    const activeMatch = wins.find((w) => !w.isBacking && t >= w.dispStart && t <= w.dispEnd);
    if (activeMatch) return activeMatch;
    if (!store.isPlaying()) {
      const selected = wins.find((w) => w.lineIdx === store.activeLineIdx() && !w.isBacking);
      if (selected) return selected;
    }
    return null;
  });

  const visibleBackingWindow = createMemo(() => {
    const t = store.currentTimeMs();
    const wins = renderWindows();
    const activeMatch = wins.find((w) => w.isBacking && t >= w.dispStart && t <= w.dispEnd);
    if (activeMatch) return activeMatch;
    if (!store.isPlaying()) {
      const selected = wins.find((w) => w.lineIdx === store.activeLineIdx() && w.isBacking);
      if (selected) return selected;
    }
    return null;
  });

  // Next upcoming line preview indicator
  const upcomingLeadWindow = createMemo(() => {
    const t = store.currentTimeMs();
    const wins = renderWindows();
    return wins.find((w) => !w.isBacking && w.dispStart > t && w.dispStart - t <= 4500) || null;
  });

  const getWipePct = (tok: Token) => {
    const t = store.currentTimeMs();
    if (t <= tok.start) return 0;
    if (t >= tok.end) return 100;
    return ((t - tok.start) / Math.max(1, tok.end - tok.start)) * 100;
  };

  const handleTogglePlay = () => {
    if (localAudioActive()) {
      const a = audioEl();
      if (a) {
        if (a.paused) {
          a.play().catch(() => {});
          store.setIsPlaying(true);
        } else {
          a.pause();
          store.setIsPlaying(false);
        }
      }
    }
    store.sendStageCommand({ type: 'cmd_toggle_play' });
  };

  const handleSeekMs = (ms: number) => {
    const clamped = Math.max(0, Math.min(store.totalDurationMs() || 300000, ms));
    store.setCurrentTimeMs(clamped);
    if (localAudioActive() && audioEl()) {
      audioEl()!.currentTime = clamped / 1000.0;
    }
    store.sendStageCommand({ type: 'cmd_seek_ms', ms: clamped });
  };

  const handleSeekRel = (deltaSec: number) => {
    const nextMs = Math.max(0, store.currentTimeMs() + deltaSec * 1000);
    handleSeekMs(nextMs);
  };

  const handleTierSwitch = (mode: '3' | '2' | '1') => {
    store.setTierMode(mode);
    store.sendStageCommand({ type: 'cmd_tier', tierMode: mode });
  };

  const handleOffsetNudge = (deltaMs: number) => {
    const next = bgOffsetMs() + deltaMs;
    store.updateBgMeta({ bg_offset_ms: next }).catch(() => {});
    store.sendStageCommand({ type: 'cmd_offset', bg_offset_ms: next });
  };

  const handleDimChange = (dim: number) => {
    store.updateBgMeta({ bg_dim_pct: dim }).catch(() => {});
    store.sendStageCommand({ type: 'cmd_dim', bg_dim_pct: dim });
  };

  const toggleFullscreen = () => {
    if (!document.fullscreenElement) {
      document.documentElement.requestFullscreen().catch(() => {});
    } else {
      document.exitFullscreen().catch(() => {});
    }
  };

  const renderAssLine = (win: RenderLineWindow, isBackingTrack: boolean) => {
    const tMode = store.tierMode();
    const showPy = win.isForeign && (tMode === '3' || tMode === '2');
    const showEn = win.isForeign && tMode === '3';
    const singleTier = !showPy && !showEn;

    const hzSize = isBackingTrack ? 54 : 64;
    const pySize = isBackingTrack ? 27 : 32;
    const enSize = isBackingTrack ? 24 : 28;

    return (
      <div
        class={`sim-ass-line ${isBackingTrack ? 'sim-backing-track' : 'sim-lead-track'}`}
        style={{
          transform: win.scaleRatio < 1 ? `scale(${win.scaleRatio.toFixed(3)})` : undefined,
          'transform-origin': isBackingTrack ? 'bottom right' : 'bottom center',
        }}
      >
        <Show
          when={!singleTier && win.isForeign}
          fallback={
            <div class="sim-single-tier-flow">
              <Show when={isBackingTrack}>
                <span class="sim-affix">( </span>
              </Show>
              <For each={win.columns}>
                {(col, idx) => {
                  const pct = () => getWipePct(col.token);
                  const isLatin = /[A-Za-z0-9]/.test(col.token.text);
                  const addSpace = idx() + 1 < win.columns.length && (col.token.space || isLatin);
                  return (
                    <span
                      class={`sim-inline-tok ${ isBackingTrack ? 'backing' : 'lead' }`}
                      style={{
                        'font-size': `${hzSize}px`,
                        '--wipe-pct': `${pct()}%`,
                        'margin-right': addSpace ? '16px' : '2px',
                      }}
                    >
                      {col.token.text}
                    </span>
                  );
                }}
              </For>
              <Show when={isBackingTrack}>
                <span class="sim-affix"> )</span>
              </Show>
            </div>
          }
        >
          <div class="sim-ruby-row">
            <For each={win.columns}>
              {(col) => {
                const pct = () => getWipePct(col.token);
                return (
                  <div
                    class={`sim-ruby-col ${isBackingTrack ? 'backing' : 'lead'}`}
                    style={{
                      width: `${col.width}px`,
                      'margin-right': `${col.gapAfter}px`,
                    }}
                  >
                    <Show when={showEn}>
                      <span
                        class="sim-tier-en"
                        style={{
                          'font-size': `${enSize}px`,
                          '--wipe-pct': `${pct()}%`,
                        }}
                      >
                        {col.enTxt}
                      </span>
                    </Show>
                    <Show when={showPy}>
                      <span
                        class="sim-tier-py"
                        style={{
                          'font-size': `${pySize}px`,
                          '--wipe-pct': `${pct()}%`,
                        }}
                      >
                        {col.pyTxt}
                      </span>
                    </Show>
                    <span
                      class="sim-tier-hz"
                      style={{
                        'font-size': `${hzSize}px`,
                        '--wipe-pct': `${pct()}%`,
                      }}
                    >
                      {col.hzTxt}
                    </span>
                  </div>
                );
              }}
            </For>
          </div>
        </Show>
      </div>
    );
  };

  return (
    <div class="detached-stage-shell">
      {/* Optional standalone audio element if user enables monitor audio in this window */}
      <Show when={store.currentVid()}>
        <audio
          ref={(el) => setAudioEl(el)}
          src={`/api/audio/${encodeURIComponent(store.currentVid())}/mix`}
          preload="auto"
          onTimeUpdate={(e) => {
            if (localAudioActive() && !e.currentTarget.paused) {
              store.updateActiveLineFromTime(Math.round(e.currentTarget.currentTime * 1000));
            }
          }}
        />
      </Show>

      {/* Top Floating Monitor Header */}
      <Show when={showHud()}>
        <header class="sim-top-bar">
          <div class="sim-top-left">
            <span class="sim-badge-live">● 1080p RENDER SIMULATOR</span>
            <span class="sim-project-title">
              {meta()?.title || store.currentVid() || 'Waiting for project...'}
            </span>
            <Show when={meta()?.artist}>
              <span class="sim-project-artist">— {meta()?.artist}</span>
            </Show>
          </div>

          <div class="sim-top-right">
            <div class="btn-group" title="Preview Tier Mode (syncs with Editor & Render)">
              <button
                class={`btn-toggle ${store.tierMode() === '3' ? 'active' : ''}`}
                onClick={() => handleTierSwitch('3')}
              >
                3-Tier
              </button>
              <button
                class={`btn-toggle ${store.tierMode() === '2' ? 'active' : ''}`}
                onClick={() => handleTierSwitch('2')}
              >
                2-Tier
              </button>
              <button
                class={`btn-toggle ${store.tierMode() === '1' ? 'active' : ''}`}
                onClick={() => handleTierSwitch('1')}
              >
                1-Tier
              </button>
            </div>

            <button
              class={`btn btn-sm ${showSafeGuides() ? 'btn-primary' : ''}`}
              onClick={() => setShowSafeGuides(!showSafeGuides())}
              title="Toggle 1920x1080 Safe Area & Subtitle Margin Guides (G)"
            >
              📐 Guides (G)
            </button>

            <button
              class={`btn btn-sm ${localAudioActive() ? 'btn-lead' : ''}`}
              onClick={() => setLocalAudioActive(!localAudioActive())}
              title="Enable local audio output in this window if the main editor tab is muted"
            >
              {localAudioActive() ? '🔊 Window Audio: ON' : '🔇 Audio via Editor'}
            </button>

            <button class="btn btn-sm" onClick={toggleFullscreen} title="Toggle Fullscreen (F)">
              ⛶ Fullscreen (F)
            </button>

            <button class="btn btn-sm" onClick={() => setShowHud(false)} title="Hide HUD Overlay (H)">
              👁️ Hide HUD (H)
            </button>
          </div>
        </header>
      </Show>

      <Show when={!showHud()}>
        <button
          class="sim-restore-hud-btn"
          onClick={() => setShowHud(true)}
          title="Show Monitor Controls (H)"
        >
          👁️ Show Controls (H)
        </button>
      </Show>

      {/* Center 16:9 1920x1080 Virtual Render Viewport */}
      <div ref={viewportWrapRef} class="sim-viewport-container" onClick={handleTogglePlay}>
        <div
          class="sim-canvas-1080p"
          style={{
            transform: `scale(${canvasScale()})`,
          }}
        >
          {/* Layer 1: Background Video / Image or Synthetic Black */}
          <Show
            when={hasBgMedia() && bgStreamUrl()}
            fallback={<div class="sim-bg-solid" />}
          >
            <div class="sim-bg-layer">
              <Show
                when={bgMediaType() === 'video'}
                fallback={<img src={bgStreamUrl()} alt="BG" class="sim-bg-media" />}
              >
                <video
                  ref={(el) => setVideoEl(el)}
                  src={bgStreamUrl()}
                  muted
                  playsinline
                  loop
                  preload="auto"
                  onLoadedMetadata={(e) => {
                    const v = e.currentTarget;
                    v.muted = true;
                    v.playbackRate = store.playbackRate();
                    const targetSec = computeTargetSec(store.currentTimeMs(), bgOffsetMs(), v.duration);
                    try {
                      v.currentTime = targetSec;
                    } catch {}
                    if (store.isPlaying()) {
                      v.play().catch(() => {});
                    }
                  }}
                  class="sim-bg-media"
                />
              </Show>
              {/* Layer 2: FFmpeg drawbox dimmer equivalent */}
              <div
                class="sim-bg-dimmer"
                style={{
                  'background-color': `rgba(0, 0, 0, ${(bgDimPct() / 100).toFixed(2)})`,
                }}
              />
            </div>
          </Show>

          {/* Optional Safe-Zone & Margin Guides */}
          <Show when={showSafeGuides()}>
            <div class="sim-guides-overlay">
              <div class="sim-guide-title-safe" />
              <div class="sim-guide-center-h" />
              <div class="sim-guide-center-v" />
              <div class="sim-guide-sub-baseline">
                <span>Lead Subtitle Baseline (y=980px / margin_bottom=100px)</span>
              </div>
            </div>
          </Show>

          {/* Next Upcoming Line subtle cue when between vocal phrases */}
          <Show when={!visibleLeadWindow() && showHud() ? upcomingLeadWindow() : null}>
            {(nextWin) => (
              <div class="sim-upcoming-cue">
                <span>Next in {Math.max(0, ((nextWin().dispStart - store.currentTimeMs()) / 1000)).toFixed(1)}s:</span>
                <strong>{nextWin().columns.map((c) => c.token.text).join(' ')}</strong>
              </div>
            )}
          </Show>

          {/* Layer 3: Backing Vocals Track (ASS bottom-right, margin_bottom=120, margin_right=80) */}
          <Show when={visibleBackingWindow()}>
            {(bWin) => (
              <div class="sim-sub-layer-backing">
                {renderAssLine(bWin(), true)}
              </div>
            )}
          </Show>

          {/* Layer 4: Lead Vocals Track (ASS bottom-center, margin_bottom=100) */}
          <Show when={visibleLeadWindow()}>
            {(lWin) => (
              <div class="sim-sub-layer-lead">
                {renderAssLine(lWin(), false)}
              </div>
            )}
          </Show>
        </div>
      </div>

      {/* Bottom Transport & Live Sync Controls */}
      <Show when={showHud()}>
        <footer class="sim-bottom-bar">
          <div class="sim-transport-left">
            <button class="btn btn-primary" onClick={handleTogglePlay}>
              {store.isPlaying() ? '⏸ Pause (Space)' : '▶ Play (Space)'}
            </button>
            <button class="btn btn-sm" onClick={() => handleSeekRel(-3)} title="Rewind 3s (J)">
              ⏪ -3s
            </button>
            <button class="btn btn-sm" onClick={() => handleSeekRel(3)} title="Forward 3s (L)">
              +3s ⏩
            </button>
            <span class="sim-timecode">
              <strong>{fmtMs(store.currentTimeMs())}</strong> / {fmtMs(store.totalDurationMs())}
            </span>
          </div>

          <div class="sim-scrubber-wrap">
            <input
              type="range"
              class="sim-scrubber"
              min="0"
              max={Math.max(1000, store.totalDurationMs())}
              value={store.currentTimeMs()}
              onInput={(e) => handleSeekMs( parseInt(e.currentTarget.value, 10) || 0 )}
            />
          </div>

          <div class="sim-transport-right">
            <Show when={hasBgMedia() && bgMediaType() === 'video'}>
              <div class="sim-sync-pill" title="Adjust Background Video Start Offset in real time">
                <span>BG Offset:</span>
                <button class="btn btn-sm" onClick={() => handleOffsetNudge(-250)}>-250ms</button>
                <span class="font-mono text-accent">{bgOffsetMs()}ms</span>
                <button class="btn btn-sm" onClick={() => handleOffsetNudge(250)}>+250ms</button>
              </div>
            </Show>

            <Show when={hasBgMedia()}>
              <div class="sim-sync-pill" title="Adjust Background Dimming (%)">
                <span>Dim: {bgDimPct()}%</span>
                <input
                  type="range"
                  min="0"
                  max="90"
                  step="5"
                  value={bgDimPct()}
                  onInput={(e) => handleDimChange(parseInt(e.currentTarget.value, 10))}
                  style={{ width: '70px' }}
                />
              </div>
            </Show>
          </div>
        </footer>
      </Show>
    </div>
  );
};
