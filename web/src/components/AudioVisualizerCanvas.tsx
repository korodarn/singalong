import { Component, createEffect, onCleanup, onMount } from 'solid-js';
import { VisualizerConfig } from '../types';

let sharedAudioCtx: AudioContext | null = null;
let sharedAnalyser: AnalyserNode | null = null;
let connectedSource: MediaElementAudioSourceNode | null = null;

export const AudioVisualizerCanvas: Component<{
  config: VisualizerConfig;
  audioElement?: HTMLAudioElement | null;
  isPlaying?: boolean;
  class?: string;
  style?: Record<string, string | number>;
}> = (props) => {
  let canvasRef!: HTMLCanvasElement;
  let animId: number = 0;

  const getAudioContext = () => {
    if (!sharedAudioCtx && typeof window !== 'undefined') {
      const AudioCtx = window.AudioContext || (window as any).webkitAudioContext;
      if (AudioCtx) {
        sharedAudioCtx = new AudioCtx();
        sharedAnalyser = sharedAudioCtx.createAnalyser();
        sharedAnalyser.fftSize = 512;
        sharedAnalyser.smoothingTimeConstant = 0.8;
      }
    }
    return { ctx: sharedAudioCtx, analyser: sharedAnalyser };
  };

  const connectAudio = (el: HTMLAudioElement) => {
    const { ctx, analyser } = getAudioContext();
    if (!ctx || !analyser) return;

    if (!connectedSource) {
      try {
        connectedSource = ctx.createMediaElementSource(el);
        connectedSource.connect(analyser);
        analyser.connect(ctx.destination);
      } catch (err) {
        // Element already connected or restricted
      }
    }

    if (ctx.state === 'suspended') {
      const resume = () => {
        ctx.resume();
        window.removeEventListener('click', resume);
        window.removeEventListener('keydown', resume);
      };
      window.addEventListener('click', resume);
      window.addEventListener('keydown', resume);
    }
  };

  createEffect(() => {
    const el = props.audioElement;
    if (el) {
      connectAudio(el);
    }
  });

  onMount(() => {
    const canvas = canvasRef;
    if (!canvas) return;
    const ctx2d = canvas.getContext('2d');
    if (!ctx2d) return;

    let syntheticPhase = 0;

    const render = () => {
      animId = requestAnimationFrame(render);

      const w = canvas.width;
      const h = canvas.height;
      if (w <= 0 || h <= 0) return;

      ctx2d.clearRect(0, 0, w, h);

      const { ctx, analyser } = getAudioContext();
      const playing = Boolean(props.isPlaying);
      const cfg = props.config;
      const type = cfg.type || 'bars';
      const col = cfg.color || '#FFD700';
      const mirror = cfg.mirror !== false;

      let freqData: Uint8Array | null = null;
      let timeData: Uint8Array | null = null;
      let hasRealAudio = false;

      if (analyser && playing) {
        freqData = new Uint8Array(analyser.frequencyBinCount);
        timeData = new Uint8Array(analyser.frequencyBinCount);
        analyser.getByteFrequencyData(freqData as any);
        analyser.getByteTimeDomainData(timeData as any);

        for (let i = 0; i < 30; i++) {
          if (freqData[i] > 10) {
            hasRealAudio = true;
            break;
          }
        }
      }

      syntheticPhase += playing ? 0.08 : 0.02;

      // Fallback synthetic wave generator for preview while paused or when audio is starting
      const getSample = (idx: number, total: number) => {
        if (hasRealAudio && freqData && timeData) {
          const binIdx = Math.floor((idx / total) * (freqData.length * 0.75));
          const freqVal = freqData[binIdx] / 255.0;
          const timeVal = (timeData[binIdx] - 128) / 128.0;
          return { freq: freqVal, time: timeVal };
        }
        // Aesthetic resting/animated pattern
        const p = idx / total;
        const s1 = Math.sin(p * Math.PI * 3 + syntheticPhase) * 0.5 + 0.5;
        const s2 = Math.sin(p * Math.PI * 7 - syntheticPhase * 1.5) * 0.3;
        const env = Math.sin(p * Math.PI); // tapering at edges
        const synthAmp = playing ? Math.max(0.15, (s1 + s2) * env) : 0.2 * env;
        return { freq: synthAmp, time: synthAmp * (Math.sin(p * 20 + syntheticPhase) * 0.8) };
      };

      ctx2d.save();

      if (type === 'bars') {
        const numBars = Math.max(16, Math.min(64, Math.floor(w / 14)));
        const barWidth = Math.max(3, (w / numBars) * 0.72);
        const gap = (w - numBars * barWidth) / (numBars + 1);

        ctx2d.fillStyle = col;
        ctx2d.shadowColor = col;
        ctx2d.shadowBlur = 6;

        for (let i = 0; i < numBars; i++) {
          const { freq } = getSample(i, numBars);
          const barH = Math.max(4, freq * (h * 0.88));
          const x = gap + i * (barWidth + gap);

          if (mirror) {
            const centerY = h / 2;
            const topY = centerY - barH / 2;
            ctx2d.beginPath();
            ctx2d.roundRect(x, topY, barWidth, barH, [3]);
            ctx2d.fill();
          } else {
            const topY = h - barH;
            ctx2d.beginPath();
            ctx2d.roundRect(x, topY, barWidth, barH, [3, 3, 0, 0]);
            ctx2d.fill();
          }
        }
      } else if (type === 'waveform' || type === 'p2p') {
        const centerY = h / 2;
        const count = Math.max(40, Math.min(180, Math.floor(w / 6)));
        const step = w / count;

        ctx2d.fillStyle = col;
        ctx2d.strokeStyle = col;
        ctx2d.shadowColor = col;
        ctx2d.shadowBlur = 8;
        ctx2d.lineWidth = 2.5;

        if (type === 'p2p') {
          // Peak-to-peak vertical envelope bars
          const barW = Math.max(2, step * 0.65);
          for (let i = 0; i < count; i++) {
            const { freq } = getSample(i, count);
            const halfH = Math.max(2, (freq * h) / 2);
            const x = i * step;
            ctx2d.beginPath();
            ctx2d.roundRect(x, centerY - halfH, barW, halfH * 2, [2]);
            ctx2d.fill();
          }
        } else {
          // Centered continuous oscillating waveform
          ctx2d.beginPath();
          ctx2d.moveTo(0, centerY);
          for (let i = 0; i <= count; i++) {
            const { time, freq } = getSample(i, count);
            const amp = Math.max(Math.abs(time), freq * 0.8);
            const y = centerY + (i % 2 === 0 ? 1 : -1) * amp * (h * 0.45);
            ctx2d.lineTo(i * step, y);
          }
          ctx2d.lineTo(w, centerY);
          ctx2d.stroke();

          // Subtle filled aura
          ctx2d.globalAlpha = 0.25;
          ctx2d.fill();
        }
      } else if (type === 'line') {
        const centerY = h / 2;
        const count = Math.max(50, Math.min(200, Math.floor(w / 4)));
        const step = w / count;

        ctx2d.strokeStyle = col;
        ctx2d.lineWidth = 3.5;
        ctx2d.shadowColor = col;
        ctx2d.shadowBlur = 10;
        ctx2d.lineCap = 'round';
        ctx2d.lineJoin = 'round';

        ctx2d.beginPath();
        for (let i = 0; i <= count; i++) {
          const { freq } = getSample(i, count);
          const y = mirror
            ? centerY - (freq - 0.5) * (h * 0.8)
            : h - 6 - freq * (h * 0.85);
          if (i === 0) ctx2d.moveTo(0, y);
          else ctx2d.lineTo(i * step, y);
        }
        ctx2d.stroke();
      } else if (type === 'circle') {
        const cx = w / 2;
        const cy = h / 2;
        const minDim = Math.min(w, h);
        const baseRadius = minDim * 0.28;
        const spikes = 64;

        ctx2d.strokeStyle = col;
        ctx2d.fillStyle = col;
        ctx2d.shadowColor = col;
        ctx2d.shadowBlur = 8;
        ctx2d.lineWidth = 2.5;

        // Inner circle
        ctx2d.beginPath();
        ctx2d.arc(cx, cy, baseRadius, 0, Math.PI * 2);
        ctx2d.stroke();

        // Radiating spokes
        for (let i = 0; i < spikes; i++) {
          const angle = (i / spikes) * Math.PI * 2;
          const { freq } = getSample(i, spikes);
          const r1 = baseRadius + 3;
          const r2 = baseRadius + 4 + freq * (minDim * 0.22);
          const x1 = cx + Math.cos(angle) * r1;
          const y1 = cy + Math.sin(angle) * r1;
          const x2 = cx + Math.cos(angle) * r2;
          const y2 = cy + Math.sin(angle) * r2;
          ctx2d.beginPath();
          ctx2d.moveTo(x1, y1);
          ctx2d.lineTo(x2, y2);
          ctx2d.stroke();
        }
      } else if (type === 'volume') {
        // Multi-segment VU meter bars
        const segments = 24;
        const segH = Math.max(3, (h - 20) / 2);
        const segW = (w - (segments + 1) * 3) / segments;

        ctx2d.fillStyle = col;
        ctx2d.shadowColor = col;
        ctx2d.shadowBlur = 4;

        for (let row = 0; row < 2; row++) {
          const rowY = 8 + row * (segH + 4);
          for (let i = 0; i < segments; i++) {
            const { freq } = getSample(i + row * 10, segments + 10);
            const activeThreshold = i / segments;
            const isActive = freq >= activeThreshold;
            ctx2d.globalAlpha = isActive ? 1.0 : 0.15;
            ctx2d.fillRect(4 + i * (segW + 3), rowY, segW, segH);
          }
        }
      }

      ctx2d.restore();
    };

    render();
  });

  onCleanup(() => {
    if (animId) cancelAnimationFrame(animId);
  });

  return (
    <canvas
      ref={canvasRef}
      width={Math.round(props.config.width || 1400)}
      height={Math.round(props.config.height || 160)}
      class={props.class}
      style={{
        display: 'block',
        width: '100%',
        height: '100%',
        'pointer-events': 'none',
        ...props.style,
      }}
    />
  );
};
