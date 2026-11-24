import { createContext, useContext } from 'react'

import { recall } from './storage'

// Simple or Full: how much of the engineering the canvas shows. Simple uses plain words, keeps the
// settings that decide how tables load and who sees what under Advanced, and hides the panels a
// data engineer runs the platform from. It changes what is shown, never the model or the code
// generated from it, and problems always show.

export type Mode = 'simple' | 'full'

export const MODE_KEY = 'murakumo.mode'

export function savedMode(): Mode {
  return recall(MODE_KEY) === 'simple' ? 'simple' : 'full'
}

export const ModeContext = createContext<Mode>('full')

export function useMode(): Mode {
  return useContext(ModeContext)
}

/** Panels Simple leaves out of the rail: running the platform rather than understanding the data. */
export const ENGINEERING_PANELS = new Set(['stack', 'marts', 'git', 'ci', 'changes'])

/** What each kind of table holds, in plain words. */
export const KIND_WORDS: Record<string, string> = {
  table: 'Rows as they are',
  fact: 'Events or transactions, one row each (an order, a trip)',
  dimension: 'Things the events are about, one row each (a customer, a product)',
  bridge: 'Links things that relate many to many',
  hub: 'Data vault: one row per business key',
  link: 'Data vault: which business keys go together',
  satellite: 'Data vault: the details of a key, with their history',
  event: 'Things that happened, kept as they arrived',
  cumulative: 'What each thing did, day by day',
  accumulating: 'A process from start to finish, one row each',
  reference: 'A short list of codes and what they mean',
}

/** What each layer is for, in plain words. */
export const LAYER_WORDS: Record<string, string> = {
  raw: 'The data as it arrives from its source',
  staging: 'The raw data cleaned: names, types, duplicates',
  intermediate: 'Steps between cleaning and the final tables',
  marts: 'The tables people report from',
  semantic: 'Metrics and their definitions',
}
