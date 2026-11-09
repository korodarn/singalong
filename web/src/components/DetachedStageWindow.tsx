import { Component, For, Show, createEffect, createMemo, createSignal, onCleanup, onMount } from 'solid-js';
import { store, fmtMs, parseTimeToMs, DEFAULT_VISUALIZER } from '../state/store';
import { Line, Token, VisualizerConfig } from '../types';
import { AudioVisualizerCanvas } from './AudioVisualizerCanvas';

const COLOR_PRESETS = [
  { name: 'Lead Gold', hex: '#FFD700' },
  { name: 'Backing Cyan', hex: '#70D6FF' },
  { name: 'Neon Green', hex: '#00FFA3' },
  { name: 'Hot Pink', hex: '#FF007F' },
  { name: 'Purple Neon', hex: '#9D4EDD' },
  { name: 'Pure White', hex: '#FFFFFF' },
  { name: 'Sunset Coral', hex: '#FF6B6B' },
  { name: 'Amber Flame', hex: '#FF9F1C' },
];

const POSITION_PRESETS: { name: string; desc: string; x: number; y: number; width: number; height: number; type?: VisualizerConfig['type'] }[] = [
  { name: 'Above Lyrics', desc: 'Centered above subtitles (1400×160)', x: 260, y: 770, width: 1400, height: 160 },
  { name: 'Bottom Strip', desc: 'Full width bottom bar (1920×80)', x: 0, y: 1000, width: 1920, height: 80 },
  { name: 'Top Banner', desc: 'Full width top edge (1920×90)', x: 0, y: 0, width: 1920, height: 90 },
  { name: 'Center Stage', desc: 'Center screen wave (1200×220)', x: 360, y: 430, width: 1200, height: 220 },
  { name: 'Center Circle', desc: 'Radial circle scope (320×320)', x: 800, y: 380, width: 320, height: 320, type: 'circle' },
];

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
  const [visAudioEl, setVisAudioEl] = createSignal<HTMLAudioElement | null>(null);
  const [isEditingVis, setIsEditingVis] = createSignal<boolean>(false);
  const [snapGuideX, setSnapGuideX] = createSignal<number | null>(null);
  const [snapGuideY, setSnapGuideY] = createSignal<number | null>(null);
  const [isDragging, setIsDragging] = createSignal<boolean>(false);

  const getSafeVis = (raw?: VisualizerConfig | null): VisualizerConfig => {
    return {
      enabled: raw?.enabled ?? DEFAULT_VISUALIZER.enabled,
      type: raw?.type || DEFAULT_VISUALIZER.type,
      x: typeof raw?.x === 'number' && !isNaN(raw.x) ? raw.x : DEFAULT_VISUALIZER.x,
      y: typeof raw?.y === 'number' && !isNaN(raw.y) ? raw.y : DEFAULT_VISUALIZER.y,
      width: typeof raw?.width === 'number' && !isNaN(raw.width) ? raw.width : DEFAULT_VISUALIZER.width,
      height: typeof raw?.height === 'number' && !isNaN(raw.height) ? raw.height : DEFAULT_VISUALIZER.height,
      color: raw?.color || DEFAULT_VISUALIZER.color,
      opacity: typeof raw?.opacity === 'number' && !isNaN(raw.opacity) ? raw.opacity : DEFAULT_VISUALIZER.opacity,
      audio_source: raw?.audio_source || DEFAULT_VISUALIZER.audio_source,
      mirror: raw?.mirror ?? DEFAULT_VISUALIZER.mirror,
    };
  };

  const [localVis, setLocalVis] = createSignal<VisualizerConfig>(
    getSafeVis(store.projectData()?.metadata?.visualizer)
  );

  // Sync from store when external changes arrive and user is not actively dragging
  createEffect(() => {
    if (isDragging()) return;
    const remote = store.projectData()?.metadata?.visualizer;
    if (remote) {
      setLocalVis(getSafeVis(remote));
    }
  });

  const updateVis = (patch: Partial<VisualizerConfig>) => {
    const next = { ...localVis(), ...patch };
    setLocalVis(next);
    store.updateVisualizer(next);
  };

  const handleVisMouseDown = (e: MouseEvent) => {
    if (!isEditingVis()) return;
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(true);

    const startX = e.clientX;
    const startY = e.clientY;
    const current = localVis();
    const initX = current.x;
    const initY = current.y;
    const initW = current.width;
    const initH = current.height;
    let curX = initX;
    let curY = initY;

    const onMove = (ev: MouseEvent) => {
      const scale = canvasScale() || 1.0;
      const dx = (ev.clientX - startX) / scale;
      const dy = (ev.clientY - startY) / scale;

      let nx = Math.round(initX + dx);
      let ny = Math.round(initY + dy);

      nx = Math.max(0, Math.min(1920 - initW, nx));
      ny = Math.max(0, Math.min(1080 - initH, ny));

      if (Math.abs(nx + initW / 2 - 960) < 18) {
        nx = 960 - Math.round(initW / 2);
        setSnapGuideX(960);
      } else {
        setSnapGuideX(null);
      }

      if (Math.abs(ny + initH / 2 - 540) < 18) {
        ny = 540 - Math.round(initH / 2);
        setSnapGuideY(540);
      } else {
        setSnapGuideY(null);
      }

      curX = nx;
      curY = ny;
      setLocalVis((prev) => ({ ...prev, x: nx, y: ny }));
    };

    const onUp = (ev: MouseEvent) => {
      ev.stopPropagation();
      setIsDragging(false);
      setSnapGuideX(null);
      setSnapGuideY(null);
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      store.updateVisualizer({ x: curX, y: curY });
    };

    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };

  const handleResizeMouseDown = (e: MouseEvent, direction: string) => {
    if (!isEditingVis()) return;
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(true);

    const startX = e.clientX;
    const startY = e.clientY;
    const current = localVis();
    const initX = current.x;
    const initY = current.y;
    const initW = current.width;
    const initH = current.height;
    const rightEdge = initX + initW;
    const bottomEdge = initY + initH;
    let finalBox = { x: initX, y: initY, width: initW, height: initH };

    const onMove = (ev: MouseEvent) => {
      const scale = canvasScale() || 1.0;
      const dx = (ev.clientX - startX) / scale;
      const dy = (ev.clientY - startY) / scale;

      let nextX = initX;
      let nextY = initY;
      let nextW = initW;
      let nextH = initH;

      if (direction.includes('e')) {
        nextW = Math.max(80, Math.min(1920 - initX, Math.round(initW + dx)));
      }
      if (direction.includes('s')) {
        nextH = Math.max(30, Math.min(1080 - initY, Math.round(initH + dy)));
      }
      if (direction.includes('w')) {
        nextX = Math.max(0, Math.min(rightEdge - 80, Math.round(initX + dx)));
        nextW = rightEdge - nextX;
      }
      if (direction.includes('n')) {
        nextY = Math.max(0, Math.min(bottomEdge - 30, Math.round(initY + dy)));
        nextH = bottomEdge - nextY;
      }

      finalBox = { x: nextX, y: nextY, width: nextW, height: nextH };
      setLocalVis((prev) => ({ ...prev, ...finalBox }));
    };

    const onUp = (ev: MouseEvent) => {
      ev.stopPropagation();
      setIsDragging(false);
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      store.updateVisualizer(finalBox);
    };

    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };

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
      } else if (e.key === 'Home') {
        e.preventDefault();
        handleSeekMs(0);
      } else if (e.key === 'f' || e.key === 'F') {
        e.preventDefault();
        toggleFullscreen();
      } else if (e.key === 'h' || e.key === 'H') {
        e.preventDefault();
        setShowHud(!showHud());
      } else if (e.key === 'g' || e.key === 'G') {
        e.preventDefault();
        setShowSafeGuides(!showSafeGuides());
      } else if (e.key === 'v' || e.key === 'V') {
        e.preventDefault();
        setIsEditingVis(!isEditingVis());
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

  // Dedicated silent audio playback for visualizer AnalyserNode
  createEffect(() => {
    const va = visAudioEl();
    if (!va) return;
    va.muted = true;
    va.playbackRate = store.playbackRate();
    const playing = store.isPlaying();
    const targetSec = store.currentTimeMs() / 1000.0;
    if (Math.abs(va.currentTime - targetSec) > 0.3) {
      try {
        va.currentTime = targetSec;
      } catch {}
    }
    if (playing) {
      va.play().catch(() => {});
    } else {
      va.pause();
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

  const [isEditingTime, setIsEditingTime] = createSignal<boolean>(false);
  const [timeInputVal, setTimeInputVal] = createSignal<string>('');

  const startEditingTime = () => {
    setTimeInputVal(fmtMs(store.currentTimeMs()));
    setIsEditingTime(true);
  };

  const cancelEditingTime = () => {
    setIsEditingTime(false);
  };

  const commitTimeInput = () => {
    if (!isEditingTime()) return;
    const val = timeInputVal().trim();
    setIsEditingTime(false);
    if (!val) return;
    const ms = parseTimeToMs(val);
    if (ms !== null) {
      handleSeekMs(ms);
    }
  };

  const handleTimeInputKeyDown = (e: KeyboardEvent) => {
    e.stopPropagation();
    if (e.key === 'Enter') {
      e.preventDefault();
      commitTimeInput();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      cancelEditingTime();
    }
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
      {/* Dedicated silent audio source feeding the visualizer AnalyserNode */}
      <Show when={store.currentVid()}>
        <audio
          ref={(el) => setVisAudioEl(el)}
          src={
            localVis().audio_source === 'vocals' && store.projectData()?.has_vocals
              ? `/api/audio/${encodeURIComponent(store.currentVid())}/vocals`
              : `/api/audio/${encodeURIComponent(store.currentVid())}/mix`
          }
          preload="auto"
          muted
        />
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
              class={`btn btn-sm ${isEditingVis() ? 'btn-lead' : localVis().enabled ? 'btn-primary' : ''}`}
              onClick={() => setIsEditingVis(!isEditingVis())}
              title="Toggle audio visualizer positioning & selection options (V)"
            >
              🎵 Visualizer {localVis().enabled ? '(ON)' : ''}
            </button>

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

        {/* Floating Visualizer Controls Toolbar when in editing mode */}
        <Show when={isEditingVis()}>
          <div class="sim-vis-toolbar" onClick={(e) => e.stopPropagation()}>
            <div class="sim-vis-toolbar-row">
              <label class="toggle-control">
                <input
                  type="checkbox"
                  checked={localVis().enabled}
                  onChange={(e) => updateVis({ enabled: e.currentTarget.checked })}
                />
                <span class="toggle-slider" />
                <strong class="text-accent text-sm">
                  {localVis().enabled ? 'Visualizer ENABLED' : 'Visualizer DISABLED'}
                </strong>
              </label>

              {/* Form Selector Pills */}
              <div class="btn-group">
                <button
                  class={`btn-toggle ${localVis().type === 'bars' ? 'active' : ''}`}
                  onClick={() => updateVis({ type: 'bars' })}
                  title="Frequency Equalizer Bars"
                >
                  📊 Bars
                </button>
                <button
                  class={`btn-toggle ${localVis().type === 'waveform' ? 'active' : ''}`}
                  onClick={() => updateVis({ type: 'waveform' })}
                  title="Centered Waveform"
                >
                  〰️ Waveform
                </button>
                <button
                  class={`btn-toggle ${localVis().type === 'line' ? 'active' : ''}`}
                  onClick={() => updateVis({ type: 'line' })}
                  title="Sound Wave Line"
                >
                  📈 Line
                </button>
                <button
                  class={`btn-toggle ${localVis().type === 'p2p' ? 'active' : ''}`}
                  onClick={() => updateVis({ type: 'p2p' })}
                  title="Peak Envelope Bars"
                >
                  📶 Peaks
                </button>
                <button
                  class={`btn-toggle ${localVis().type === 'circle' ? 'active' : ''}`}
                  onClick={() => updateVis({ type: 'circle' })}
                  title="Radial Oscilloscope Circle"
                >
                  ⭕ Circle
                </button>
                <button
                  class={`btn-toggle ${localVis().type === 'volume' ? 'active' : ''}`}
                  onClick={() => updateVis({ type: 'volume' })}
                  title="Stereo VU Meter"
                >
                  🎚️ VU
                </button>
              </div>

              {/* Color Swatches */}
              <div class="sim-vis-color-swatches">
                <For each={COLOR_PRESETS}>
                  {(item) => (
                    <button
                      class={`vis-color-dot ${(localVis().color || '#FFD700').toLowerCase() === item.hex.toLowerCase() ? 'active' : ''}`}
                      style={{ 'background-color': item.hex }}
                      onClick={() => updateVis({ color: item.hex })}
                      title={item.name}
                    />
                  )}
                </For>
                <input
                  type="color"
                  value={localVis().color || '#FFD700'}
                  onInput={(e) => setLocalVis((prev) => ({ ...prev, color: e.currentTarget.value }))}
                  onChange={(e) => updateVis({ color: e.currentTarget.value })}
                  class="sim-vis-color-pick"
                  title="Custom color"
                />
              </div>

              {/* Opacity */}
              <div class="sim-sync-pill">
                <span>Opacity: {Math.round((localVis().opacity ?? 0.85) * 100)}%</span>
                <input
                  type="range"
                  min="10"
                  max="100"
                  step="5"
                  value={Math.round((localVis().opacity ?? 0.85) * 100)}
                  onInput={(e) => {
                    const val = parseInt(e.currentTarget.value, 10) / 100.0;
                    setLocalVis((prev) => ({ ...prev, opacity: val }));
                  }}
                  onChange={(e) => {
                    const val = parseInt(e.currentTarget.value, 10) / 100.0;
                    updateVis({ opacity: val });
                  }}
                  style={{ width: '60px' }}
                />
              </div>

              {/* Audio Source */}
              <div class="btn-group">
                <button
                  class={`btn-toggle ${localVis().audio_source !== 'vocals' ? 'active' : ''}`}
                  onClick={() => updateVis({ audio_source: 'mix' })}
                  title="Visualize entire mix"
                >
                  🎵 Mix
                </button>
                <button
                  class={`btn-toggle ${localVis().audio_source === 'vocals' ? 'active' : ''}`}
                  onClick={() => updateVis({ audio_source: 'vocals' })}
                  title="Visualize vocal track only"
                >
                  🎤 Vocals
                </button>
              </div>

              <button
                class="btn btn-primary btn-sm ml-auto"
                onClick={() => setIsEditingVis(false)}
              >
                ✓ Done Editing
              </button>
            </div>

            {/* Sub row: presets and numeric coordinates */}
            <div class="sim-vis-toolbar-row sub-row">
              <span class="text-muted text-xs">Presets:</span>
              <div class="btn-group">
                <For each={POSITION_PRESETS}>
                  {(p) => (
                    <button
                      class="btn btn-sm btn-ghost"
                      onClick={() => updateVis({ x: p.x, y: p.y, width: p.width, height: p.height, ...(p.type ? { type: p.type } : {}) })}
                      title={p.desc}
                    >
                      {p.name}
                    </button>
                  )}
                </For>
              </div>

              <div class="sim-vis-numeric-inputs">
                <span class="text-xs text-muted">X:</span>
                <input
                  type="number"
                  class="sim-vis-num-input"
                  value={localVis().x}
                  onInput={(e) => {
                    const v = parseInt(e.currentTarget.value, 10);
                    if (!isNaN(v)) setLocalVis((prev) => ({ ...prev, x: v }));
                  }}
                  onChange={(e) => {
                    const v = parseInt(e.currentTarget.value, 10);
                    if (!isNaN(v)) updateVis({ x: v });
                  }}
                />
                <span class="text-xs text-muted">Y:</span>
                <input
                  type="number"
                  class="sim-vis-num-input"
                  value={localVis().y}
                  onInput={(e) => {
                    const v = parseInt(e.currentTarget.value, 10);
                    if (!isNaN(v)) setLocalVis((prev) => ({ ...prev, y: v }));
                  }}
                  onChange={(e) => {
                    const v = parseInt(e.currentTarget.value, 10);
                    if (!isNaN(v)) updateVis({ y: v });
                  }}
                />
                <span class="text-xs text-muted">W:</span>
                <input
                  type="number"
                  class="sim-vis-num-input"
                  value={localVis().width}
                  onInput={(e) => {
                    const v = parseInt(e.currentTarget.value, 10);
                    if (!isNaN(v)) setLocalVis((prev) => ({ ...prev, width: v }));
                  }}
                  onChange={(e) => {
                    const v = parseInt(e.currentTarget.value, 10);
                    if (!isNaN(v)) updateVis({ width: v });
                  }}
                />
                <span class="text-xs text-muted">H:</span>
                <input
                  type="number"
                  class="sim-vis-num-input"
                  value={localVis().height}
                  onInput={(e) => {
                    const v = parseInt(e.currentTarget.value, 10);
                    if (!isNaN(v)) setLocalVis((prev) => ({ ...prev, height: v }));
                  }}
                  onChange={(e) => {
                    const v = parseInt(e.currentTarget.value, 10);
                    if (!isNaN(v)) updateVis({ height: v });
                  }}
                />
                <button
                  class="btn btn-xs btn-secondary"
                  onClick={() => updateVis({ x: Math.round((1920 - localVis().width) / 2) })}
                  title="Center horizontally"
                >
                  ⬌ Center X
                </button>
                <button
                  class="btn btn-xs btn-secondary"
                  onClick={() => updateVis({ y: Math.round((1080 - localVis().height) / 2) })}
                  title="Center vertically"
                >
                  ⬍ Center Y
                </button>
              </div>
            </div>
          </div>
        </Show>
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

          {/* Layer 2.5: Audio Visualizer Layer (Interactive Drag & Resize on 1080p Canvas) */}
          <Show when={localVis().enabled || isEditingVis()}>
            <div
              class={`sim-visualizer-container ${isEditingVis() ? 'is-editing' : ''}`}
              style={{
                position: 'absolute',
                left: `${localVis().x}px`,
                top: `${localVis().y}px`,
                width: `${localVis().width}px`,
                height: `${localVis().height}px`,
                opacity: localVis().opacity ?? 0.85,
                'pointer-events': isEditingVis() ? 'auto' : 'none',
                cursor: isEditingVis() ? 'move' : 'default',
                'z-index': 15,
              }}
              onMouseDown={handleVisMouseDown}
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
              }}
            >
              <AudioVisualizerCanvas
                config={localVis()}
                audioElement={visAudioEl()}
                isPlaying={store.isPlaying()}
              />

              {/* Interactive Bounding Box and Resize Handles in Edit Mode */}
              <Show when={isEditingVis()}>
                <div
                  class="sim-vis-edit-box"
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                  }}
                >
                  <div class="sim-vis-badge">
                    📍 {localVis().type.toUpperCase()} • X:{localVis().x} Y:{localVis().y} | {localVis().width}×{localVis().height} px
                  </div>
                  <div class="sim-vis-drag-cue">✥ Drag to move</div>

                  {/* 8 resize handles */}
                  <div class="vis-handle handle-nw" onMouseDown={(e) => handleResizeMouseDown(e, 'nw')} onClick={(e) => e.stopPropagation()} />
                  <div class="vis-handle handle-n"  onMouseDown={(e) => handleResizeMouseDown(e, 'n')}  onClick={(e) => e.stopPropagation()} />
                  <div class="vis-handle handle-ne" onMouseDown={(e) => handleResizeMouseDown(e, 'ne')} onClick={(e) => e.stopPropagation()} />
                  <div class="vis-handle handle-e"  onMouseDown={(e) => handleResizeMouseDown(e, 'e')}  onClick={(e) => e.stopPropagation()} />
                  <div class="vis-handle handle-se" onMouseDown={(e) => handleResizeMouseDown(e, 'se')} onClick={(e) => e.stopPropagation()} />
                  <div class="vis-handle handle-s"  onMouseDown={(e) => handleResizeMouseDown(e, 's')}  onClick={(e) => e.stopPropagation()} />
                  <div class="vis-handle handle-sw" onMouseDown={(e) => handleResizeMouseDown(e, 'sw')} onClick={(e) => e.stopPropagation()} />
                  <div class="vis-handle handle-w"  onMouseDown={(e) => handleResizeMouseDown(e, 'w')}  onClick={(e) => e.stopPropagation()} />
                </div>
              </Show>
            </div>
          </Show>

          {/* Snap alignment guides */}
          <Show when={snapGuideX() !== null}>
            <div class="sim-snap-guide-x" style={{ left: `${snapGuideX()}px` }}>
              <span>Center X (960px)</span>
            </div>
          </Show>
          <Show when={snapGuideY() !== null}>
            <div class="sim-snap-guide-y" style={{ top: `${snapGuideY()}px` }}>
              <span>Center Y (540px)</span>
            </div>
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
            <button class="btn btn-sm btn-restart" onClick={() => handleSeekMs(0)} title="Restart from beginning (00:00.00) (Home)">
              ⏮ Reset
            </button>
            <span class="sim-timecode">
              <Show
                when={isEditingTime()}
                fallback={
                  <strong
                    class="time-editable"
                    onClick={startEditingTime}
                    tabIndex={0}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        startEditingTime();
                      }
                    }}
                    title="Click to edit current position, press Enter to jump"
                  >
                    {fmtMs(store.currentTimeMs())}
                  </strong>
                }
              >
                <input
                  ref={(el) => {
                    setTimeout(() => {
                      el.focus();
                      el.select();
                    }, 20);
                  }}
                  type="text"
                  class="time-edit-input"
                  value={timeInputVal()}
                  onInput={(e) => setTimeInputVal(e.currentTarget.value)}
                  onKeyDown={handleTimeInputKeyDown}
                  onBlur={commitTimeInput}
                  title="Enter time (e.g. 01:23.45, 1:23, or 45), press Enter to seek"
                />
              </Show>
              {' '} / {fmtMs(store.totalDurationMs())}
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
