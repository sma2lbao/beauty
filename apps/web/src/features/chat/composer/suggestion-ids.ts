/** 稳定的 listbox/option id;aria-activedescendant 与 DOM id 共用。 */
export const SUGGESTIONS_ID = 'composer-suggestions';

export function suggestionOptionId(key: string) {
  return `${SUGGESTIONS_ID}-option-${key.replace(/[^a-zA-Z0-9_-]/g, '-')}`;
}
