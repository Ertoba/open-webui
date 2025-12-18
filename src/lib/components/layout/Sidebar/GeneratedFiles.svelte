<script lang="ts">
	import { getContext, onDestroy, onMount } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import { user } from '$lib/stores';
	import { WEBUI_BASE_URL } from '$lib/constants';
	import { getFiles } from '$lib/apis/files';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import { getGeneratedFiles, onGeneratedFilesChange, type GeneratedFileEntry } from '$lib/utils/generatedFiles';

	const i18n = getContext<Writable<i18nType>>('i18n');

	type UiFile = {
		id: string;
		name: string;
		type: 'image' | 'audio' | 'video' | 'pdf' | 'file';
		url: string;
		createdAt: number;
		fileId?: string;
	};

	let loading = false;
	let files: UiFile[] = [];

	const iconForType = (type: UiFile['type']) => {
		if (type === 'image') return '🖼️';
		if (type === 'audio') return '🎧';
		if (type === 'video') return '🎬';
		if (type === 'pdf') return '📄';
		return '📎';
	};

	const normalizeUrl = (url: string) => {
		if (!url) return '';
		if (url.startsWith('http://') || url.startsWith('https://') || url.startsWith('data:')) return url;
		return `${WEBUI_BASE_URL}${url.startsWith('/') ? '' : '/'}${url}`;
	};

	const typeFromMime = (mime: string | undefined): UiFile['type'] => {
		const m = (mime ?? '').toLowerCase();
		if (m.startsWith('image/')) return 'image';
		if (m.startsWith('audio/')) return 'audio';
		if (m.startsWith('video/')) return 'video';
		if (m === 'application/pdf') return 'pdf';
		return 'file';
	};

	const mapSession = (entry: GeneratedFileEntry): UiFile | null => {
		const rawUrl = entry?.fileId ? `/api/v1/files/${entry.fileId}/content` : entry?.url;
		if (!rawUrl) return null;
		return {
			id: entry.id,
			name: entry.name || entry.id,
			type: entry.type === 'py_photo' ? 'image' : (entry.type as UiFile['type']),
			url: normalizeUrl(rawUrl),
			createdAt: entry.createdAt ?? Date.now(),
			fileId: entry.fileId
		};
	};

	const mapBackend = (file: any): UiFile | null => {
		const fileId = String(file?.id ?? '').trim();
		if (!fileId) return null;

		const metaData = file?.meta?.data ?? {};
		const generated = Boolean(metaData?.generated);
		const source = String(metaData?.source ?? '').trim();
		if (!generated && !source) return null;

		const name = String(file?.filename ?? file?.meta?.name ?? fileId);
		const contentType = String(file?.meta?.content_type ?? '');

		return {
			id: fileId,
			name,
			type: typeFromMime(contentType),
			url: `${WEBUI_BASE_URL}/api/v1/files/${fileId}/content`,
			createdAt: Number(file?.created_at ?? Date.now()),
			fileId
		};
	};

	const refresh = async () => {
		loading = true;
		try {
			const sessionFiles = getGeneratedFiles().map(mapSession).filter(Boolean) as UiFile[];

			let backendFiles: UiFile[] = [];
			if (localStorage.token && $user) {
				const res = await getFiles(localStorage.token);
				backendFiles = (Array.isArray(res) ? res : []).map(mapBackend).filter(Boolean) as UiFile[];
			}

			const combined = [...backendFiles, ...sessionFiles].filter(
				(f, idx, arr) =>
					arr.findIndex((x) => (x.fileId && f.fileId ? x.fileId === f.fileId : x.url === f.url)) === idx
			);

			combined.sort((a, b) => (b.createdAt ?? 0) - (a.createdAt ?? 0));
			files = combined.slice(0, 200);
		} catch {
			files = getGeneratedFiles().map(mapSession).filter(Boolean) as UiFile[];
		} finally {
			loading = false;
		}
	};

	let unsubscribe: null | (() => void) = null;
	let removeListener: null | (() => void) = null;

	onMount(() => {
		removeListener = onGeneratedFilesChange(() => void refresh());
		unsubscribe = user.subscribe(() => void refresh());
		void refresh();
	});

	onDestroy(() => {
		unsubscribe?.();
		removeListener?.();
	});
</script>

{#if loading}
	<div class="px-2 py-2 text-xs text-gray-500 dark:text-gray-400 flex items-center gap-2">
		<Spinner className="size-3.5" />{$i18n.t('Loading...')}
	</div>
{:else if files.length === 0}
	<div class="px-3 py-2 text-xs text-gray-500 dark:text-gray-400">{$i18n.t('No files')}</div>
{:else}
	<div class="px-2 pb-2 flex flex-col gap-1.5 max-h-56 overflow-y-auto scrollbar-thin">
		{#each files as f (f.id)}
			<div
				class="flex items-center gap-2 rounded-xl px-2 py-1.5 hover:bg-gray-100 dark:hover:bg-gray-900 transition"
			>
				<div class="shrink-0 text-sm leading-none" aria-hidden="true">{iconForType(f.type)}</div>

				{#if f.type === 'image'}
					<img
						src={f.url}
						alt={f.name}
						class="size-7 rounded-lg object-cover bg-black/10 dark:bg-white/10"
						loading="lazy"
					/>
				{/if}

				{#if f.type === 'audio'}
					<audio class="h-8 w-32" controls src={f.url} preload="none"></audio>
				{/if}

				<div class="min-w-0 flex-1">
					<div class="text-xs text-gray-900 dark:text-gray-100 truncate">{f.name}</div>
					<div class="text-[11px] text-gray-500 dark:text-gray-400 truncate">{f.type}</div>
				</div>

				<div class="shrink-0 flex items-center gap-2 text-[11px]">
					<a
						href={f.url}
						target="_blank"
						rel="noreferrer"
						class="text-gray-600 dark:text-gray-300 hover:underline"
					>
						{$i18n.t('Open')}
					</a>
					<a
						href={f.url}
						download={f.name}
						class="text-gray-600 dark:text-gray-300 hover:underline"
					>
						{$i18n.t('Download')}
					</a>
				</div>
			</div>
		{/each}
	</div>
{/if}
