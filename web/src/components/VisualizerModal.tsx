import { Component, For, Show } from 'solid-js';
import { store, DEFAULT_VISUALIZER } from '../state/store';
import { VisualizerConfig } from '../types';

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

const POSITION_PRESETS: { name: string; desc: string; config: Partial<VisualizerConfig> }[] = [
  {
    name: 'Above Lyrics (Recommended)',
    desc: 'Centered 1400×160 band just above subtitle baseline',
    config: { x: 260, y: 770, width: 1400, height: 160 },
  },
  {
    name: 'Bottom Strip',
    desc: 'Full-width 1920×80 subtle bar below subtitles',
    config: { x: 0, y: 1000, width: 1920, height: 80 },
  },
  {
    name: 'Top Banner',
    desc: 'Full-width 1920×90 ambient visualizer along the top edge',
    config: { x: 0, y: 0, width: 1920, height: 90 },
  },
  {
    name: 'Center Stage',
    desc: 'Prominent 1200×220 audio wave band across screen center',
    config: { x: 360, y: 430, width: 1200, height: 220 },
  },
  {
    name: 'Center Lissajous Circle',
    desc: 'Symmetric 320×320 oscilloscope sphere in the center',
    config: { x: 800, y: 380, width: 320, height: 320, type: 'circle' },
  },
];

export const VisualizerModal: Component = () => {
  const vis = (): VisualizerConfig => {
    return store.projectData()?.metadata?.visualizer || DEFAULT_VISUALIZER;
  };

  const isEnabled = () => vis().enabled;

  const handleUpdate = (patch: Partial<VisualizerConfig>) => {
    store.updateVisualizer(patch);
  };

  const handleOpenMonitor = () => {
    store.setShowVisualizerModal(false);
    store.openStagePopupWindow();
  };

  return (
    <Show when={store.showVisualizerModal()}>
      <div class="modal-overlay" onClick={() => store.setShowVisualizerModal(false)}>
        <div class="modal-card visualizer-modal" onClick={(e) => e.stopPropagation()}>
          <header class="modal-header">
            <div class="flex-row items-center gap-2">
              <span class="text-xl">🎵</span>
              <div>
                <h3 class="modal-title">Audio Waveform & Spectrum Visualizer</h3>
                <p class="modal-subtitle">
                  Configure visualizer style, colors, and placement for live preview & rendered video
                </p>
              </div>
            </div>
            <button
              class="modal-close-btn"
              onClick={() => store.setShowVisualizerModal(false)}
              title="Close"
            >
              ×
            </button>
          </header>

          <div class="modal-body">
            {/* Enable switch */}
            <div class="vis-opt-row vis-enable-row">
              <label class="toggle-control">
                <input
                  type="checkbox"
                  checked={isEnabled()}
                  onChange={(e) => handleUpdate({ enabled: e.currentTarget.checked })}
                />
                <span class="toggle-slider" />
                <span class="toggle-label font-bold text-accent">
                  {isEnabled() ? 'Audio Visualizer is ENABLED' : 'Audio Visualizer is DISABLED'}
                </span>
              </label>
              <button
                class="btn btn-secondary btn-sm"
                onClick={handleOpenMonitor}
                title="Open separate 1080p monitor window with drag & resize editing"
              >
                📺 Open Pop-Out Monitor (Edit Position)
              </button>
            </div>

            {/* Visualizer Forms */}
            <div class="vis-section">
              <label class="vis-section-label">Visualizer Form / Style</label>
              <div class="vis-type-grid">
                <button
                  class={`vis-type-card ${vis().type === 'bars' ? 'active' : ''}`}
                  onClick={() => handleUpdate({ type: 'bars' })}
                >
                  <span class="vis-type-icon">📊</span>
                  <strong class="vis-type-name">Frequency Bars</strong>
                  <span class="vis-type-desc">Equalizer spectrum bars with dynamic peaks</span>
                </button>

                <button
                  class={`vis-type-card ${vis().type === 'waveform' ? 'active' : ''}`}
                  onClick={() => handleUpdate({ type: 'waveform' })}
                >
                  <span class="vis-type-icon">〰️</span>
                  <strong class="vis-type-name">Centered Waveform</strong>
                  <span class="vis-type-desc">Oscillating sound wave centered horizontally</span>
                </button>

                <button
                  class={`vis-type-card ${vis().type === 'line' ? 'active' : ''}`}
                  onClick={() => handleUpdate({ type: 'line' })}
                >
                  <span class="vis-type-icon">📈</span>
                  <strong class="vis-type-name">Spectrum Line</strong>
                  <span class="vis-type-desc">Continuous flowing neon audio wave line</span>
                </button>

                <button
                  class={`vis-type-card ${vis().type === 'p2p' ? 'active' : ''}`}
                  onClick={() => handleUpdate({ type: 'p2p' })}
                >
                  <span class="vis-type-icon">📶</span>
                  <strong class="vis-type-name">Peak Envelope (P2P)</strong>
                  <span class="vis-type-desc">Solid peak-to-peak audio waveform bars</span>
                </button>

                <button
                  class={`vis-type-card ${vis().type === 'circle' ? 'active' : ''}`}
                  onClick={() => handleUpdate({ type: 'circle' })}
                >
                  <span class="vis-type-icon">⭕</span>
                  <strong class="vis-type-name">Lissajous Scope</strong>
                  <span class="vis-type-desc">Radial circular audio oscilloscope</span>
                </button>

                <button
                  class={`vis-type-card ${vis().type === 'volume' ? 'active' : ''}`}
                  onClick={() => handleUpdate({ type: 'volume' })}
                >
                  <span class="vis-type-icon">🎚️</span>
                  <strong class="vis-type-name">VU Meter</strong>
                  <span class="vis-type-desc">Segmented stereo volume level meter</span>
                </button>
              </div>
            </div>

            {/* Colors & Appearance */}
            <div class="vis-section">
              <label class="vis-section-label">Color & Opacity</label>
              <div class="vis-color-row">
                <For each={COLOR_PRESETS}>
                  {(item) => (
                    <button
                      class={`vis-color-swatch ${vis().color.toLowerCase() === item.hex.toLowerCase() ? 'active' : ''}`}
                      style={{ 'background-color': item.hex }}
                      onClick={() => handleUpdate({ color: item.hex })}
                      title={item.name}
                    />
                  )}
                </For>
                <div class="vis-custom-color-wrap">
                  <input
                    type="color"
                    value={vis().color || '#FFD700'}
                    onInput={(e) => handleUpdate({ color: e.currentTarget.value })}
                    class="vis-color-input"
                    title="Custom color picker"
                  />
                  <input
                    type="text"
                    value={vis().color || '#FFD700'}
                    onInput={(e) => handleUpdate({ color: e.currentTarget.value })}
                    class="vis-hex-input"
                    placeholder="#RRGGBB"
                  />
                </div>
              </div>

              <div class="vis-sliders-grid">
                <div class="vis-field">
                  <div class="vis-field-header">
                    <span>Opacity: {Math.round((vis().opacity ?? 0.85) * 100)}%</span>
                  </div>
                  <input
                    type="range"
                    min="10"
                    max="100"
                    step="5"
                    value={Math.round((vis().opacity ?? 0.85) * 100)}
                    onInput={(e) => handleUpdate({ opacity: parseInt(e.currentTarget.value, 10) / 100.0 })}
                  />
                </div>

                <div class="vis-field">
                  <div class="vis-field-header">
                    <span>Audio Source</span>
                  </div>
                  <div class="btn-group">
                    <button
                      class={`btn-toggle ${vis().audio_source !== 'vocals' ? 'active' : ''}`}
                      onClick={() => handleUpdate({ audio_source: 'mix' })}
                      title="Visualize entire mix track"
                    >
                      🎵 Full Mix
                    </button>
                    <button
                      class={`btn-toggle ${vis().audio_source === 'vocals' ? 'active' : ''}`}
                      onClick={() => handleUpdate({ audio_source: 'vocals' })}
                      title="Visualize only the vocal stems"
                    >
                      🎤 Vocals Only
                    </button>
                  </div>
                </div>

                <div class="vis-field">
                  <div class="vis-field-header">
                    <span>Symmetry / Mirror</span>
                  </div>
                  <label class="toggle-control">
                    <input
                      type="checkbox"
                      checked={vis().mirror !== false}
                      onChange={(e) => handleUpdate({ mirror: e.currentTarget.checked })}
                    />
                    <span class="toggle-slider" />
                    <span class="text-sm">{vis().mirror !== false ? 'Mirrored Vertically' : 'Single Direction'}</span>
                  </label>
                </div>
              </div>
            </div>

            {/* Position Presets */}
            <div class="vis-section">
              <label class="vis-section-label">Quick Position Presets (1920×1080)</label>
              <div class="vis-presets-grid">
                <For each={POSITION_PRESETS}>
                  {(preset) => (
                    <button
                      class="btn btn-secondary vis-preset-btn"
                      onClick={() => handleUpdate(preset.config)}
                    >
                      <div class="vis-preset-title">{preset.name}</div>
                      <div class="vis-preset-desc">{preset.desc}</div>
                    </button>
                  )}
                </For>
              </div>
            </div>

            {/* Exact Coordinates */}
            <div class="vis-section">
              <div class="flex-row items-center justify-between mb-2">
                <label class="vis-section-label mb-0">Exact Coordinates (1920×1080 Frame)</label>
                <div class="flex-row gap-1">
                  <button
                    class="btn btn-sm btn-ghost"
                    onClick={() => handleUpdate({ x: Math.round((1920 - vis().width) / 2) })}
                    title="Center Horizontally"
                  >
                    ⬌ Center X
                  </button>
                  <button
                    class="btn btn-sm btn-ghost"
                    onClick={() => handleUpdate({ y: Math.round((1080 - vis().height) / 2) })}
                    title="Center Vertically"
                  >
                    ⬍ Center Y
                  </button>
                </div>
              </div>

              <div class="vis-coords-grid">
                <div class="vis-coord-item">
                  <span class="vis-coord-label">X (Left)</span>
                  <input
                    type="number"
                    min="0"
                    max="1920"
                    value={vis().x}
                    onInput={(e) => handleUpdate({ x: parseInt(e.currentTarget.value, 10) || 0 })}
                    class="vis-coord-input"
                  />
                </div>
                <div class="vis-coord-item">
                  <span class="vis-coord-label">Y (Top)</span>
                  <input
                    type="number"
                    min="0"
                    max="1080"
                    value={vis().y}
                    onInput={(e) => handleUpdate({ y: parseInt(e.currentTarget.value, 10) || 0 })}
                    class="vis-coord-input"
                  />
                </div>
                <div class="vis-coord-item">
                  <span class="vis-coord-label">Width</span>
                  <input
                    type="number"
                    min="50"
                    max="1920"
                    value={vis().width}
                    onInput={(e) => handleUpdate({ width: parseInt(e.currentTarget.value, 10) || 100 })}
                    class="vis-coord-input"
                  />
                </div>
                <div class="vis-coord-item">
                  <span class="vis-coord-label">Height</span>
                  <input
                    type="number"
                    min="20"
                    max="1080"
                    value={vis().height}
                    onInput={(e) => handleUpdate({ height: parseInt(e.currentTarget.value, 10) || 40 })}
                    class="vis-coord-input"
                  />
                </div>
              </div>
            </div>

            {/* Note about separate window */}
            <div class="vis-hint-banner">
              <span>💡 <strong>Want visual drag & drop repositioning?</strong></span>
              <p>
                Open the separate <strong>Pop-Out Video Monitor</strong> window to visually drag and resize the visualizer directly on the 1080p canvas with live audio feedback.
              </p>
            </div>
          </div>

          <footer class="modal-footer">
            <button
              class="btn btn-primary"
              onClick={handleOpenMonitor}
            >
              📺 Open Pop-Out Monitor & Edit Position
            </button>
            <button
              class="btn btn-secondary"
              onClick={() => store.setShowVisualizerModal(false)}
            >
              Done
            </button>
          </footer>
        </div>
      </div>
    </Show>
  );
};
