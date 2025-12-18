import { PDF_GENERATOR_API_BASE_URL } from '$lib/constants';

export type PdfGeneratorStatus = {
	available: boolean;
	enabled: boolean;
};

export type PdfGeneratorConfig = {
	ENABLE_PDF_GENERATOR: boolean;
};

export const getPdfGeneratorConfig = async (token: string = ''): Promise<PdfGeneratorConfig> => {
	let error: string | null = null;

	const res = await fetch(`${PDF_GENERATOR_API_BASE_URL}/config`, {
		method: 'GET',
		credentials: 'include',
		headers: {
			Accept: 'application/json',
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

export const updatePdfGeneratorConfig = async (
	token: string = '',
	payload: PdfGeneratorConfig
): Promise<PdfGeneratorConfig> => {
	let error: string | null = null;

	const res = await fetch(`${PDF_GENERATOR_API_BASE_URL}/config/update`, {
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

export const getPdfGeneratorStatus = async (token: string = ''): Promise<PdfGeneratorStatus> => {
	let error: string | null = null;

	const res = await fetch(`${PDF_GENERATOR_API_BASE_URL}/status`, {
		method: 'GET',
		credentials: 'include',
		headers: {
			Accept: 'application/json',
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

export type PdfGenerateResponse = {
	id: string;
	media_type: string;
	view_url: string;
	download_url: string;
	file_id?: string | null;
};

export const generatePdf = async (
	token: string = '',
	payload: { input: string; title?: string; message_id?: string }
): Promise<PdfGenerateResponse> => {
	let error: string | null = null;

	const res = await fetch(`${PDF_GENERATOR_API_BASE_URL}/generate`, {
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
