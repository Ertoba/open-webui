<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import Switch from '$lib/components/common/Switch.svelte';

	import {
		getPdfGeneratorConfig,
		updatePdfGeneratorConfig,
		type PdfGeneratorConfig
	} from '$lib/apis/pdf_generator';

	const i18n = getContext<Writable<i18nType>>('i18n');

	export let saveHandler: () => void;

	let loading = false;
	let saving = false;

	let ENABLE_PDF_GENERATOR = true;

	const load = async () => {
		loading = true;
		try {
			const cfg = (await getPdfGeneratorConfig(localStorage.token)) as PdfGeneratorConfig;
			ENABLE_PDF_GENERATOR = Boolean(cfg?.ENABLE_PDF_GENERATOR);
		} catch (e) {
			toast.error(`${e}`);
		} finally {
			loading = false;
		}
	};

	const save = async () => {
		saving = true;
		try {
			await updatePdfGeneratorConfig(localStorage.token, {
				ENABLE_PDF_GENERATOR
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
		<div class="text-lg font-semibold">{$i18n.t('PDF Generator')}</div>
		<div class="text-sm text-gray-500 dark:text-gray-400">
			{$i18n.t('Convert assistant text into a downloadable PDF')}
		</div>
	</div>

	{#if loading}
		<div class="flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400">
			<Spinner className="size-4" />{$i18n.t('Loading...')}
		</div>
	{:else}
		<div class="flex items-center justify-between gap-4 rounded-xl border border-gray-100 dark:border-gray-800 p-4">
			<div class="flex flex-col">
				<div class="font-medium">{$i18n.t('Enable PDF Generator')}</div>
				<div class="text-xs text-gray-500 dark:text-gray-400">
					{$i18n.t('Allow users to generate PDFs from assistant responses')}
				</div>
			</div>

			<Switch
				state={ENABLE_PDF_GENERATOR}
				on:change={(e) => {
					ENABLE_PDF_GENERATOR = e.detail;
				}}
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

