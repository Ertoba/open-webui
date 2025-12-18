export type GeneratedFileType = 'image' | 'audio' | 'video' | 'pdf' | 'py_photo' | 'file';

export type GeneratedFileEntry = {
	id: string;
	type: GeneratedFileType;
	name: string;
	url: string;
	createdAt: number;
	mimeType?: string;
	source?: string;
	fileId?: string;
};

const STORAGE_KEY = 'owui_generated_files';
const CHANGE_EVENT = 'generated-files:changed';

const canUseStorage = () => typeof window !== 'undefined';

const readEntries = (): GeneratedFileEntry[] => {
	if (!canUseStorage()) return [];
	try {
		const raw = window.sessionStorage.getItem(STORAGE_KEY);
		if (!raw) return [];
		const parsed = JSON.parse(raw);
		return Array.isArray(parsed) ? parsed : [];
	} catch {
		return [];
	}
};

const writeEntries = (entries: GeneratedFileEntry[]) => {
	if (!canUseStorage()) return;
	try {
		window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(entries));
		window.dispatchEvent(new CustomEvent(CHANGE_EVENT));
	} catch {
		// ignore
	}
};

export const getGeneratedFiles = (): GeneratedFileEntry[] => readEntries();

export const addGeneratedFile = (entry: GeneratedFileEntry) => {
	if (!entry?.id || !entry?.url) return;
	const entries = readEntries();

	const next = [entry, ...entries].filter(
		(e, idx, arr) => arr.findIndex((x) => (x.fileId && e.fileId ? x.fileId === e.fileId : x.url === e.url)) === idx
	);

	writeEntries(next.slice(0, 200));
};

export const clearGeneratedFiles = () => writeEntries([]);

export const onGeneratedFilesChange = (handler: () => void) => {
	if (!canUseStorage()) return () => {};
	const listener = () => handler();
	window.addEventListener(CHANGE_EVENT, listener as EventListener);
	return () => window.removeEventListener(CHANGE_EVENT, listener as EventListener);
};

