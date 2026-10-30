import { Component, createSignal, Show } from 'solid-js';
import { store } from '../state/store';
import { downloadBgMedia, setLocalBgMedia, uploadBgMedia } from '../api';

export const VideoBgModal: Component = () => {
  const [activeTab, setActiveTab] = createSignal<'url' | 'upload' | 'local'>('url');
  const [urlInput, setUrlInput] = createSignal<string>('');
  const [localPathInput, setLocalPathInput] = createSignal<string>('');
  const [isLoading, setIsLoading] = createSignal<boolean>(false);
  const [errorMsg, setErrorMsg] = createSignal<string>('');
  const [isDragOver, setIsDragOver] = createSignal<boolean>(false);

  const meta = () => store.projectData()?.metadata;
  const currentBgPath = () => meta()?.bg_media_path || meta()?.bg_video_path || '';
  const currentBgType = () => meta()?.bg_media_type || (currentBgPath().match(/\.(jpe?g|png|webp|bmp)$/i) ? 'image' : 'video');
  const currentStreamUrl = () => meta()?.bg_stream_url || (currentBgPath() ? `/api/bg-media/stream?path=${encodeURIComponent(currentBgPath().split('/').pop() || '')}` : '');
  const offsetMs = () => meta()?.bg_offset_ms ?? 0;
  const dimPct = () => meta()?.bg_dim_pct ?? 35;

  const handleClose = () => {
    store.setShowVideoBgModal(false);
    setErrorMsg('');
  };

  const handleDownloadUrl = async () => {
    const url = urlInput().trim();
    if (!url) {
      setErrorMsg('Please enter a video URL, YouTube link, or image URL.');
      return;
    }
    const vid = store.currentVid();
    setIsLoading(true);
    setErrorMsg('');
    try {
      const res = await downloadBgMedia(url, vid);
      if (res.ok) {
        await store.updateBgMeta({
          bg_media_path: res.bg_path,
          bg_media_type: res.media_type,
          bg_video_id: res.media_type === 'video' ? res.bg_id : null,
          bg_video_path: res.media_type === 'video' ? res.bg_path : null,
          bg_stream_url: res.stream_url,
          bg_media_exists: true,
        });
        setUrlInput('');
        store.addToast(`🎬 Set background ${res.media_type}: ${res.bg_id}`);
      }
    } catch (e: any) {
      setErrorMsg(e.message || 'Failed to download background media');
    } finally {
      setIsLoading(false);
    }
  };

  const handleLocalPath = async () => {
    const p = localPathInput().trim();
    if (!p) {
      setErrorMsg('Please enter a local file path.');
      return;
    }
    const vid = store.currentVid();
    setIsLoading(true);
    setErrorMsg('');
    try {
      const res = await setLocalBgMedia(p, vid);
      if (res.ok) {
        await store.updateBgMeta({
          bg_media_path: res.bg_path,
          bg_media_type: res.media_type,
          bg_video_id: res.media_type === 'video' ? res.bg_id : null,
          bg_video_path: res.media_type === 'video' ? res.bg_path : null,
          bg_stream_url: res.stream_url,
          bg_media_exists: true,
        });
        setLocalPathInput('');
        store.addToast(`🎬 Set background ${res.media_type}: ${res.bg_id}`);
      }
    } catch (e: any) {
      setErrorMsg(e.message || 'Failed to load local background media');
    } finally {
      setIsLoading(false);
    }
  };

  const handleFileUpload = async (file: File) => {
    if (!file) return;
    const vid = store.currentVid();
    setIsLoading(true);
    setErrorMsg('');
    try {
      const res = await uploadBgMedia(file, vid);
      if (res.ok) {
        await store.updateBgMeta({
          bg_media_path: res.bg_path,
          bg_media_type: res.media_type,
          bg_video_id: res.media_type === 'video' ? res.bg_id : null,
          bg_video_path: res.media_type === 'video' ? res.bg_path : null,
          bg_stream_url: res.stream_url,
          bg_media_exists: true,
        });
        store.addToast(`🎬 Uploaded background ${res.media_type}: ${file.name}`);
      }
    } catch (e: any) {
      setErrorMsg(e.message || 'Failed to upload background media');
    } finally {
      setIsLoading(false);
    }
  };

  const handleRemoveBg = async () => {
    try {
      await store.updateBgMeta({
        bg_media_path: null,
        bg_media_type: null,
        bg_video_id: null,
        bg_video_path: null,
        bg_stream_url: null,
        bg_media_exists: false,
      });
      store.addToast('🗑️ Cleared background media');
    } catch (e: any) {
      setErrorMsg(e.message);
    }
  };

  const handleOffsetChange = (val: number) => {
    store.updateBgMeta({ bg_offset_ms: val });
  };

  const handleDimChange = (val: number) => {
    store.updateBgMeta({ bg_dim_pct: val });
  };

  return (
    <Show when={store.showVideoBgModal()}>
      <div class="modal-backdrop" onClick={handleClose}>
        <div class="modal-card modal-large" onClick={(e) => e.stopPropagation()}>
          <div class="modal-header">
            <h3>🎬 Background Media & Synchronization</h3>
            <button class="btn-close" onClick={handleClose}>✕</button>
          </div>

          <div class="modal-body" style={{ 'display': 'flex', 'flex-direction': 'column', 'gap': '16px' }}>
            {/* Active Media Status Banner */}
            <Show
              when={currentBgPath()}
              fallback={
                <div class="bg-empty-banner">
                  <span>ℹ️ No background media set (using solid black). Select a video (.mp4/.webm) or image (.jpg/.png) below.</span>
                </div>
              }
            >
              <div class="bg-active-card">
                <div class="bg-active-header">
                  <div class="bg-active-info">
                    <span class={`badge-media ${currentBgType()}`}>
                      {currentBgType() === 'image' ? '🖼️ STATIC IMAGE' : '🎥 VIDEO'}
                    </span>
                    <span class="bg-file-name" title={currentBgPath()}>
                      {currentBgPath().split('/').pop()}
                    </span>
                  </div>
                  <button class="btn btn-sm btn-danger" onClick={handleRemoveBg} disabled={isLoading()}>
                    🗑️ Remove Background
                  </button>
                </div>

                <div class="bg-preview-row">
                  <div class="bg-mini-preview">
                    <Show
                      when={currentBgType() === 'video'}
                      fallback={
                        <img
                          src={currentStreamUrl()}
                          alt="Background preview"
                          class="bg-preview-media"
                        />
                      }
                    >
                      <video
                        src={currentStreamUrl()}
                        controls
                        muted
                        class="bg-preview-media"
                      />
                    </Show>
                  </div>

                  <div class="bg-adjust-controls">
                    <Show when={currentBgType() === 'video'}>
                      <div class="form-group">
                        <label class="form-label" style={{ 'display': 'flex', 'justify-content': 'space-between' }}>
                          <span>⏱️ Video Start Offset:</span>
                          <span class="font-mono text-accent">{offsetMs()} ms ({((offsetMs() || 0) / 1000).toFixed(2)}s)</span>
                        </label>
                        <div class="nudge-btn-row">
                          <button class="btn btn-sm" onClick={() => handleOffsetChange(offsetMs() - 1000)}>-1.0s</button>
                          <button class="btn btn-sm" onClick={() => handleOffsetChange(offsetMs() - 100)}>-100ms</button>
                          <input
                            type="number"
                            class="form-input font-mono"
                            style={{ width: '90px', 'text-align': 'center' }}
                            value={offsetMs()}
                            onInput={(e) => handleOffsetChange(parseInt(e.currentTarget.value, 10) || 0)}
                          />
                          <button class="btn btn-sm" onClick={() => handleOffsetChange(offsetMs() + 100)}>+100ms</button>
                          <button class="btn btn-sm" onClick={() => handleOffsetChange(offsetMs() + 1000)}>+1.0s</button>
                          <button class="btn btn-sm" onClick={() => handleOffsetChange(0)}>Reset</button>
                        </div>
                        <span class="form-hint">Adjust so cutscenes or beats line up with the audio.</span>
                      </div>
                    </Show>

                    <div class="form-group">
                      <label class="form-label" style={{ 'display': 'flex', 'justify-content': 'space-between' }}>
                        <span>🌓 Background Dimming:</span>
                        <span class="font-mono text-accent">{dimPct()}%</span>
                      </label>
                      <input
                        type="range"
                        min="0"
                        max="90"
                        step="5"
                        value={dimPct()}
                        onInput={(e) => handleDimChange(parseInt(e.currentTarget.value, 10))}
                        style={{ width: '100%' }}
                      />
                      <span class="form-hint">Darkens background layer to make karaoke lyrics easily readable.</span>
                    </div>
                  </div>
                </div>
              </div>
            </Show>

            {/* Source Selection Tabs */}
            <div class="bg-source-section">
              <div class="tab-header">
                <button
                  class={`tab-btn ${activeTab() === 'url' ? 'active' : ''}`}
                  onClick={() => setActiveTab('url')}
                >
                  🌐 Download Link (YouTube / URL)
                </button>
                <button
                  class={`tab-btn ${activeTab() === 'upload' ? 'active' : ''}`}
                  onClick={() => setActiveTab('upload')}
                >
                  📁 Upload Local File
                </button>
                <button
                  class={`tab-btn ${activeTab() === 'local' ? 'active' : ''}`}
                  onClick={() => setActiveTab('local')}
                >
                  💻 Local File Path
                </button>
              </div>

              <div class="tab-content">
                <Show when={activeTab() === 'url'}>
                  <div class="tab-pane">
                    <p class="tab-desc">
                      Paste a YouTube URL/ID or any direct link to a video (<code>.mp4</code>, <code>.webm</code>) or image (<code>.jpg</code>, <code>.png</code>).
                    </p>
                    <div class="input-with-button">
                      <input
                        type="text"
                        class="form-input"
                        placeholder="https://www.youtube.com/watch?v=... or direct .mp4/.png URL"
                        value={urlInput()}
                        onInput={(e) => setUrlInput(e.currentTarget.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') handleDownloadUrl();
                        }}
                        disabled={isLoading()}
                      />
                      <button
                        class="btn btn-primary"
                        onClick={handleDownloadUrl}
                        disabled={isLoading() || !urlInput().trim()}
                      >
                        {isLoading() ? '⏳ Downloading...' : '⬇️ Download & Set'}
                      </button>
                    </div>
                  </div>
                </Show>

                <Show when={activeTab() === 'upload'}>
                  <div class="tab-pane">
                    <p class="tab-desc">
                      Drag & drop or select a video (<code>.mp4</code>, <code>.webm</code>) or static image (<code>.jpg</code>, <code>.png</code>, <code>.webp</code>) from your computer.
                    </p>
                    <div
                      class={`upload-dropzone ${isDragOver() ? 'drag-over' : ''}`}
                      onDragOver={(e) => {
                        e.preventDefault();
                        setIsDragOver(true);
                      }}
                      onDragLeave={() => setIsDragOver(false)}
                      onDrop={(e) => {
                        e.preventDefault();
                        setIsDragOver(false);
                        const file = e.dataTransfer?.files?.[0];
                        if (file) handleFileUpload(file);
                      }}
                    >
                      <input
                        type="file"
                        id="bg-file-picker"
                        accept="video/mp4,video/webm,video/quicktime,video/x-matroska,image/png,image/jpeg,image/webp"
                        style={{ display: 'none' }}
                        onChange={(e) => {
                          const file = e.currentTarget.files?.[0];
                          if (file) handleFileUpload(file);
                        }}
                        disabled={isLoading()}
                      />
                      <div class="dropzone-inner">
                        <span class="dropzone-icon">🎬</span>
                        <span class="dropzone-title">Drag & drop video or image file here</span>
                        <span class="dropzone-subtitle">or</span>
                        <label for="bg-file-picker" class="btn btn-lead" style={{ cursor: 'pointer' }}>
                          Browse Local File...
                        </label>
                      </div>
                    </div>
                  </div>
                </Show>

                <Show when={activeTab() === 'local'}>
                  <div class="tab-pane">
                    <p class="tab-desc">
                      Enter the absolute or relative file path on the machine (e.g. <code>work/test.mp4</code> or <code>/home/user/bg.png</code>).
                    </p>
                    <div class="input-with-button">
                      <input
                        type="text"
                        class="form-input font-mono"
                        placeholder="/path/to/video.mp4 or work/bg/image.png"
                        value={localPathInput()}
                        onInput={(e) => setLocalPathInput(e.currentTarget.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') handleLocalPath();
                        }}
                        disabled={isLoading()}
                      />
                      <button
                        class="btn btn-primary"
                        onClick={handleLocalPath}
                        disabled={isLoading() || !localPathInput().trim()}
                      >
                        {isLoading() ? '⏳ Checking...' : '✅ Set Local Path'}
                      </button>
                    </div>
                  </div>
                </Show>
              </div>
            </div>

            <Show when={errorMsg()}>
              <div class="error-banner">
                <span>⚠️ {errorMsg()}</span>
              </div>
            </Show>
          </div>

          <div class="modal-footer">
            <button class="btn btn-primary" onClick={handleClose}>
              Done
            </button>
          </div>
        </div>
      </div>
    </Show>
  );
};
