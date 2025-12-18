<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';
	import Switch from '$lib/components/common/Switch.svelte';

	import { getMusicConfig, updateMusicConfig, type MusicConfig } from '$lib/apis/music';

	const i18n = getContext<Writable<i18nType>>('i18n');

	export let saveHandler: () => void;

	let loading = false;
	let saving = false;

	let ELEVENLABS_MUSIC_ENABLED = true;
	let ELEVENLABS_API_KEY = '';
	let ELEVENLABS_MUSIC_MODE = 'detailed';
	let ELEVENLABS_MUSIC_DEFAULT_FORMAT = 'mp3_44100_128';
	let ELEVENLABS_MUSIC_MODEL_ID = 'music_v1';
	let ELEVENLABS_MUSIC_DEFAULT_LENGTH_MS = 30000;
	let ELEVENLABS_MUSIC_MAX_LENGTH_MS = 120000;

	const OUTPUT_FORMATS = [
		'mp3_44100_128',
		'mp3_44100_192',
		'mp3_44100_320',
		'wav_44100'
	];

	const load = async () => {
		loading = true;
		try {
			const cfg = (await getMusicConfig(localStorage.token)) as MusicConfig;
			ELEVENLABS_MUSIC_ENABLED = Boolean(cfg?.ELEVENLABS_MUSIC_ENABLED);
			ELEVENLABS_API_KEY = cfg?.ELEVENLABS_API_KEY ?? '';
			ELEVENLABS_MUSIC_MODE = cfg?.ELEVENLABS_MUSIC_MODE ?? 'detailed';
			ELEVENLABS_MUSIC_DEFAULT_FORMAT = cfg?.ELEVENLABS_MUSIC_DEFAULT_FORMAT ?? 'mp3_44100_128';
			ELEVENLABS_MUSIC_MODEL_ID = cfg?.ELEVENLABS_MUSIC_MODEL_ID ?? 'music_v1';
			ELEVENLABS_MUSIC_DEFAULT_LENGTH_MS = Number(cfg?.ELEVENLABS_MUSIC_DEFAULT_LENGTH_MS ?? 30000);
			ELEVENLABS_MUSIC_MAX_LENGTH_MS = Number(cfg?.ELEVENLABS_MUSIC_MAX_LENGTH_MS ?? 120000);
		} catch (e) {
			toast.error(`${e}`);
		} finally {
			loading = false;
		}
	};

	const save = async () => {
		saving = true;
		try {
			await updateMusicConfig(localStorage.token, {
				ELEVENLABS_MUSIC_ENABLED,
				ELEVENLABS_API_KEY,
				ELEVENLABS_MUSIC_MODE: 'detailed',
				ELEVENLABS_MUSIC_DEFAULT_FORMAT,
				ELEVENLABS_MUSIC_MODEL_ID,
				ELEVENLABS_MUSIC_DEFAULT_LENGTH_MS,
				ELEVENLABS_MUSIC_MAX_LENGTH_MS
			});

			saveHandler?.();
		} catch (e) {
			toast.error(`${e}`);
		} finally {
			saving = false;
		}
	};

	onMount(() => {
		void load();
	});
</script>

<div class="flex flex-col gap-4 pb-16">
	<div>
		<div class="text-lg font-semibold">{$i18n.t('ElevenLabs Music')}</div>
		<div class="text-sm text-gray-500 dark:text-gray-400">
			{$i18n.t('Generate music using the ElevenLabs Music API (non-streaming).')}
		</div>
	</div>

	{#if loading}
		<div class="flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400">
			<Spinner className="size-4" />{$i18n.t('Loading...')}
		</div>
	{:else}
		<div class="flex items-center justify-between gap-4 rounded-xl border border-gray-100 dark:border-gray-800 p-4">
			<div class="flex flex-col">
				<div class="font-medium">{$i18n.t('Enable ElevenLabs Music')}</div>
				<div class="text-xs text-gray-500 dark:text-gray-400">
					{$i18n.t('Allow users to generate music from chat prompts (no credits)')}
				</div>
			</div>

			<Switch
				state={ELEVENLABS_MUSIC_ENABLED}
				on:change={(e) => {
					ELEVENLABS_MUSIC_ENABLED = e.detail;
				}}
			/>
		</div>

		<div class="flex flex-col gap-2">
			<div class="text-sm font-medium">{$i18n.t('ElevenLabs API Key')}</div>
			<SensitiveInput bind:value={ELEVENLABS_API_KEY} placeholder={$i18n.t('Enter API key')} />
		</div>

		<div class="flex flex-col gap-2">
			<div class="text-sm font-medium">{$i18n.t('Default Output Format')}</div>
			<select
				class="w-full rounded-xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 px-3 py-2 text-sm outline-hidden"
				bind:value={ELEVENLABS_MUSIC_DEFAULT_FORMAT}
			>
				{#each OUTPUT_FORMATS as fmt}
					<option value={fmt}>{fmt}</option>
				{/each}
			</select>
		</div>

		<div class="flex flex-col gap-2">
			<div class="text-sm font-medium">{$i18n.t('Music Mode')}</div>
			<input
				class="w-full rounded-xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 px-3 py-2 text-sm outline-hidden opacity-70"
				value={ELEVENLABS_MUSIC_MODE || 'detailed'}
				readonly
			/>
			<div class="text-xs text-gray-500 dark:text-gray-400">
				{$i18n.t('Streaming is disabled; mode is fixed to detailed')}
			</div>
		</div>

		<div class="flex flex-col gap-2">
			<div class="text-sm font-medium">{$i18n.t('Default Model')}</div>
			<input
				class="w-full rounded-xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 px-3 py-2 text-sm outline-hidden"
				placeholder="music_v1"
				bind:value={ELEVENLABS_MUSIC_MODEL_ID}
			/>
		</div>

		<div class="flex flex-col gap-2">
			<div class="text-sm font-medium">{$i18n.t('Default Music Length (ms)')}</div>
			<input
				type="number"
				min="1000"
				step="1000"
				class="w-full rounded-xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 px-3 py-2 text-sm outline-hidden"
				bind:value={ELEVENLABS_MUSIC_DEFAULT_LENGTH_MS}
			/>
			<div class="text-xs text-gray-500 dark:text-gray-400">
				{$i18n.t('If not provided by the client, backend uses 30000ms')}
			</div>
		</div>

		<div class="flex flex-col gap-2">
			<div class="text-sm font-medium">{$i18n.t('Max Music Length (ms)')}</div>
			<input
				type="number"
				min="1000"
				step="1000"
				class="w-full rounded-xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 px-3 py-2 text-sm outline-hidden"
				bind:value={ELEVENLABS_MUSIC_MAX_LENGTH_MS}
			/>
		</div>

		<div class="flex justify-end">
			<button
				type="button"
				class="px-4 py-2 rounded-xl bg-gray-900 hover:bg-gray-800 text-white dark:bg-white dark:hover:bg-gray-100 dark:text-gray-900 text-sm font-medium disabled:opacity-60 disabled:cursor-not-allowed"
				disabled={saving}
				on:click={() => void save()}
			>
				{#if saving}
					<span class="inline-flex items-center gap-2">
						<Spinner className="size-4" />{$i18n.t('Saving...')}
					</span>
				{:else}
					{$i18n.t('Save')}
				{/if}
			</button>
		</div>
	{/if}
</div>
