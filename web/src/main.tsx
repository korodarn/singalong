import { Component, For, onMount } from 'solid-js';
import { render } from 'solid-js/web';
import { store } from './state/store';
import { HeaderBar } from './components/HeaderBar';
import { KaraokeStage } from './components/KaraokeStage';
import { WaveformPane } from './components/WaveformPane';
import { LyricTable } from './components/LyricTable';
import { PhoneticModal } from './components/PhoneticModal';
import { CommentModal } from './components/CommentModal';
import { IngestModal } from './components/IngestModal';
import { RenderModal } from './components/RenderModal';
import { VideoBgModal } from './components/VideoBgModal';
import { DetachedStageWindow } from './components/DetachedStageWindow';
import './style.css';

const App: Component = () => {
  let waveControls: {
    seekToMs: (ms: number) => void;
    playBounds: (startMs: number, endMs?: number) => void;
  } | null = null;

  onMount(async () => {
    const list = await store.loadProjectList();
    const urlParams = new URLSearchParams(window.location.search);
    const target = urlParams.get('vid') || urlParams.get('project');
    if (target) {
      await store.selectProject(target);
    } else if (list.length > 0) {
      await store.selectProject(list[0].id);
    } else if (!store.isStageWindow) {
      store.setShowIngestModal(true);
    }
  });

  if (store.isStageWindow) {
    return <DetachedStageWindow />;
  }

  const handleSeek = (ms: number) => {
    waveControls?.seekToMs(ms);
  };

  const handlePlayBounds = (startMs: number, endMs?: number) => {
    waveControls?.playBounds(startMs, endMs);
  };

  const handleSelectAndSeekLine = (idx: number) => {
    const ln = store.lines()[idx];
    if (ln) handleSeek(Math.max(0, ln.start - 200));
  };

  return (
    <div class="app-layout">
      <HeaderBar onSelectAndSeekLine={handleSelectAndSeekLine} />
      <main class="workbench">
        <KaraokeStage onPlayBounds={handlePlayBounds} />
        <WaveformPane onRegisterControls={(c) => (waveControls = c)} />
        <LyricTable onSeekTo={handleSeek} onPlayBounds={handlePlayBounds} />
      </main>

      <PhoneticModal />
      <CommentModal />
      <IngestModal />
      <RenderModal />
      <VideoBgModal />

      {/* Toast Notifications */}
      <div class="toast-container">
        <For each={store.toasts()}>
          {(t) => <div class="toast-msg">{t.message}</div>}
        </For>
      </div>
    </div>
  );
};

render(() => <App />, document.getElementById('root')!);
