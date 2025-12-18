import { MUSIC_API_BASE_URL } from '$lib/constants';

export type MusicStatus = {
	available: boolean;
	enabled: boolean;
	configured: boolean;
	redis_available: boolean;
	credits_required: boolean;
	default_model: string;
};

export type MusicConfig = {
	ELEVENLABS_MUSIC_ENABLED: boolean;
	ELEVENLABS_API_KEY: string;
	ELEVENLABS_MUSIC_MODE: string;
	ELEVENLABS_MUSIC_DEFAULT_FORMAT: string;
	ELEVENLABS_MUSIC_MODEL_ID: string;
	ELEVENLABS_MUSIC_DEFAULT_LENGTH_MS: number;
	ELEVENLABS_MUSIC_MAX_LENGTH_MS: number;
};

export const getMusicStatus = async (token: string = ''): Promise<MusicStatus> => {
	let error: string | null = null;

	const res = await fetch(`${MUSIC_API_BASE_URL}/status`, {
		method: 'GET',
		credentials: 'include',
		headers: {
			'Content-Type': 'application/json',
			...(token && { Authorization: `Bearer ${token}` })
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err?.detail ?? 'Server connection failed';
			return null;
		});

	if (error) throw error;
	return res;
};

export const getMusicConfig = async (token: string = ''): Promise<MusicConfig> => {
	let error: string | null = null;

	const res = await fetch(`${MUSIC_API_BASE_URL}/config`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { Authorization: `Bearer ${token}` })
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err?.detail ?? 'Server connection failed';
			return null;
		});

	if (error) throw error;
	return res;
};

export const updateMusicConfig = async (token: string = '', payload: MusicConfig): Promise<MusicConfig> => {
	let error: string | null = null;

	const res = await fetch(`${MUSIC_API_BASE_URL}/config/update`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { Authorization: `Bearer ${token}` })
		},
		body: JSON.stringify(payload)
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err?.detail ?? 'Server connection failed';
			return null;
		});

	if (error) throw error;
	return res;
};

export type MusicGenerateResponse = {
	status?: 'pending';
	id: string;
	play_url: string;
	download_url: string;
	ext?: string;
	media_type?: string;
	charged?: boolean;
	file_id?: string | null;
};

export type MusicPendingResponse = {
	status: 'pending';
};

export type MusicGenerateResult = Omit<MusicGenerateResponse, 'status'>;

export const generateMusic = async (
	token: string = '',
	payload: {
		request_id: string;
		prompt?: string;
		composition_plan?: string;
		music_length_ms?: number;
		output_format?: string;
		force_instrumental?: boolean;
		model_id?: string;
		chat_id: string;
		message_id: string;
	}
): Promise<MusicPendingResponse | MusicGenerateResult> => {
	let error: string | null = null;

	const res = await fetch(`${MUSIC_API_BASE_URL}/generate`, {
		method: 'POST',
		credentials: 'include',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { Authorization: `Bearer ${token}` })
		},
		body: JSON.stringify(payload)
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err?.detail ?? 'Server connection failed';
			return null;
		});

	if (error) throw error;
	return res;
};

export const getMusicRequest = async (
	token: string = '',
	request_id: string
): Promise<MusicPendingResponse | MusicGenerateResult> => {
	let error: string | null = null;

	const res = await fetch(`${MUSIC_API_BASE_URL}/requests/${request_id}`, {
		method: 'GET',
		credentials: 'include',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { Authorization: `Bearer ${token}` })
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err?.detail ?? 'Server connection failed';
			return null;
		});

	if (error) throw error;
	return res;
};
