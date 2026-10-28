import { ProjectData, ProjectSummary } from './types';

export async function fetchProjects(): Promise<ProjectSummary[]> {
  const res = await fetch('/api/projects');
  if (!res.ok) throw new Error('Failed to fetch projects');
  return res.json();
}

export async function fetchProject(vid: string): Promise<ProjectData> {
  const res = await fetch(`/api/project/${encodeURIComponent(vid)}`);
  if (!res.ok) throw new Error(`Project ${vid} not found`);
  return res.json();
}

export async function saveProject(
  vid: string,
  lines: any[],
  customDictUpdates?: Record<string, Record<string, { pinyin: string; en: string }>>
) {
  const res = await fetch(`/api/project/${encodeURIComponent(vid)}/save`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      lines,
      custom_dict_updates: customDictUpdates,
    }),
  });
  if (!res.ok) throw new Error('Failed to save project');
  return res.json();
}

export async function updateCustomDict(entries: Record<string, Record<string, { pinyin: string; en: string }>>) {
  const res = await fetch('/api/dict', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(entries),
  });
  if (!res.ok) throw new Error('Failed to update custom dictionary');
  return res.json();
}

export function subscribePipelineSSE(
  params: { url: string; query?: string; skip_demucs?: boolean; skip_netease?: boolean; force_lrc?: boolean },
  onEvent: (data: any) => void,
  onError: (err: any) => void
): () => void {
  const q = new URLSearchParams();
  q.set('url', params.url);
  if (params.query) q.set('query', params.query);
  if (params.skip_demucs) q.set('skip_demucs', '1');
  if (params.skip_netease) q.set('skip_netease', '1');
  if (params.force_lrc) q.set('force_lrc', '1');

  const es = new EventSource(`/api/pipeline/run?${q.toString()}`);
  es.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data);
      onEvent(data);
      if (data.type === 'complete' || data.type === 'error') {
        es.close();
      }
    } catch (err) {
      onError(err);
    }
  };
  es.onerror = (e) => {
    onError(e);
    es.close();
  };
  return () => es.close();
}

export function subscribeRenderSSE(
  vid: string,
  previewSec: number | null,
  assOnly: boolean,
  onEvent: (data: any) => void,
  onError: (err: any) => void
): () => void {
  const q = new URLSearchParams();
  if (previewSec) q.set('preview', String(previewSec));
  if (assOnly) q.set('ass_only', '1');

  const es = new EventSource(`/api/project/${encodeURIComponent(vid)}/render?${q.toString()}`);
  es.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data);
      onEvent(data);
      if (data.type === 'complete' || data.type === 'error') {
        es.close();
      }
    } catch (err) {
      onError(err);
    }
  };
  es.onerror = (e) => {
    onError(e);
    es.close();
  };
  return () => es.close();
}
