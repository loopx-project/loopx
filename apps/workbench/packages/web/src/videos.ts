import type { Api, VideoRef } from './api';
import { t } from './i18n';

export const MAX_VIDEOS = 3;
export const MAX_VIDEO_BYTES = 100 * 1024 * 1024;
export const VIDEO_ACCEPT = 'video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm';
export const MAX_FILE_BYTES = 100 * 1024 * 1024;
/** How many uploads, videos and other files together, one message may carry. */
export const MAX_UPLOADS = 10;
/**
 * A file waiting to be uploaded with a message: a video, or any other file. Only a video has a
 * preview URL; other files are sent to the agent by path and never rendered here.
 */
export interface PreparedVideo { id: string; name: string; file: File; previewUrl: string }

export const isPlayable = (upload: PreparedVideo): boolean => upload.previewUrl !== '';

export const isVideo = (file: Pick<File, 'type' | 'name'>): boolean => file.type.startsWith('video/') || /\.(mp4|mov|webm)$/i.test(file.name);

/** A video the hub stores and plays as a video; any other file, an AVI included, goes as a plain file. */
export const isSupportedVideo = (file: Pick<File, 'type' | 'name'>): boolean => isVideo(file) && (/\.(mp4|mov|webm)$/i.test(file.name) || ['video/mp4', 'video/quicktime', 'video/webm'].includes(file.type));

export function prepareVideo(file: File): PreparedVideo {
  if (!isSupportedVideo(file)) throw new Error(t('videos.wrongType'));
  if (!file.size) throw new Error(t('videos.empty'));
  if (file.size > MAX_VIDEO_BYTES) throw new Error(t('videos.tooLarge'));
  return { id: `${Date.now()}-${Math.random().toString(36).slice(2)}`, name: file.name || t('videos.video'), file, previewUrl: URL.createObjectURL(file) };
}

export function prepareFile(file: File): PreparedVideo {
  if (!file.size) throw new Error(t('uploads.empty', file.name || t('uploads.file')));
  if (file.size > MAX_FILE_BYTES) throw new Error(t('uploads.tooLarge', file.name || t('uploads.file')));
  return { id: `${Date.now()}-${Math.random().toString(36).slice(2)}`, name: file.name || t('uploads.file'), file, previewUrl: '' };
}

export async function restoreVideos(api: Api, threadId: string, videos: VideoRef[] = []): Promise<PreparedVideo[]> {
  const files = await Promise.all(videos.map(video => api.videoFile(threadId, video)));
  return files.map((file, i) => videos[i].mimeType.startsWith('video/') ? prepareVideo(file) : prepareFile(file));
}
