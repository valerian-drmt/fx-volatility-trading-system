/**
 * Fetched list data, defended at the edge (#348): the static type says array,
 * but a payload that is an object or an error body must render as "no rows",
 * not crash the view. Returns the value when it is an array, else [].
 */
export function asArray<T>(value: readonly T[] | null | undefined): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}
