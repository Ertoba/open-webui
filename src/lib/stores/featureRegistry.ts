import { browser } from '$app/environment';
import { writable } from 'svelte/store';

export type ActiveGenerationMode = 'music' | 'video' | 'photo' | null;

export type ChatFeatureRegistryEntry = {
	activeGenerationMode: ActiveGenerationMode;
	updatedAt: number;
};

export type FeatureRegistryState = Record<string, ChatFeatureRegistryEntry>;

const STORAGE_KEY = 'owui_feature_registry_v1';

const readState = (): FeatureRegistryState => {
	if (!browser) return {};
	try {
		const raw = window.sessionStorage.getItem(STORAGE_KEY);
		if (!raw) return {};
		const parsed = JSON.parse(raw);
		if (!parsed || typeof parsed !== 'object') return {};
		return parsed as FeatureRegistryState;
	} catch {
		return {};
	}
};

export const featureRegistry = writable<FeatureRegistryState>(readState());

if (browser) {
	featureRegistry.subscribe((value) => {
		try {
			window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(value));
		} catch {
			// ignore
		}
	});
}

export const setChatGenerationMode = (chatId: string, mode: ActiveGenerationMode) => {
	const id = String(chatId ?? '').trim();
	if (!id) return;
	featureRegistry.update((state) => {
		const prev = state[id];
		const next: ChatFeatureRegistryEntry = {
			activeGenerationMode: mode,
			updatedAt: Date.now()
		};

		// Avoid noisy updates.
		if (prev?.activeGenerationMode === next.activeGenerationMode) return state;
		return { ...state, [id]: next };
	});
};

export const getChatGenerationMode = (
	state: FeatureRegistryState | undefined,
	chatId: string
): ActiveGenerationMode => {
	const id = String(chatId ?? '').trim();
	if (!id) return null;
	return state?.[id]?.activeGenerationMode ?? null;
};

