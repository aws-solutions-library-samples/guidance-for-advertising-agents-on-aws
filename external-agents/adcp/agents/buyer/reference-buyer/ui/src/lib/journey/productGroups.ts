/**
 * Grouping a fan-out's products into the Discover panel's format groups.
 *
 * The counting rules are the whole of this module, and each exists because the alternative would
 * state something false:
 *
 *   A group's count is `products.length`. It is derived, never supplied, so the number of icons on
 *   screen is the number of products in the record.
 *
 *   A format with no products produces NO GROUP. Not an empty one, not a zero. An empty group would
 *   assert "this seller offers no podcast inventory" when the truth is "podcast was not part of this
 *   answer".
 *
 *   A seller that errored is counted as errored, separately from one that returned an empty list.
 *   "Could not be asked" and "has nothing" are different facts about that seller's business.
 *
 *   Truncation travels with the count. "8 products" and "8 of 200 recorded" are different claims.
 */

import {
  productCpm,
  productFacts,
  productFormatInfo,
  productPriceLabel,
  productReach,
  productRelevance,
  productSimilarityBars,
  productTitle,
} from '../products';
import { parseFanOut, sellerEntryFailed, sellerEntryName } from '../sellers';
import { FORMAT_KEYS, FORMAT_META, type FormatKey } from './phases';
import { readListTruncation } from './exchanges';
import type { DiscoverData, FormatGroup, FormatGroupProduct, TruncationNote } from './types';
import type { Product as WireProduct, SellerEntry } from '../types';

/**
 * Which authored format group a product belongs to.
 *
 * Segment matching against the format id, the same approach `productFormatInfo` documents. Prefix or
 * suffix matching misses real ids: triton's `audio_30s` names its medium in the FIRST segment, and an
 * `endsWith`/`includes('_' + medium)` test silently missed it. Returns null rather than forcing a
 * product into a group it does not belong to.
 */
export function formatKeyFor(product: WireProduct): FormatKey | null {
  const { id } = productFormatInfo(product);
  if (!id) return null;
  for (const segment of id.toLowerCase().split('_')) {
    if ((FORMAT_KEYS as readonly string[]).includes(segment)) return segment as FormatKey;
  }
  return null;
}

interface Bucket {
  products: FormatGroupProduct[];
  sellers: string[];
  truncation?: TruncationNote;
}

/**
 * Reduce one wire product to the fields the panel labels it with.
 *
 * Optional fields are spread in only when the seller supplied them, rather than set to a placeholder:
 * the row then simply has nothing to say about a price the seller never quoted. `position` is the
 * product's index in its own seller's list, which is what makes the key unique even if a seller
 * repeats a product_id.
 *
 * `facts`, `reach` and `similarity` are what a row needs to be distinguishable from its neighbours.
 * They were all on the wire and all discarded here, which is why ten Gotham products differing by
 * placement, edition and device rendered as ten identical rows. `productReach` and
 * `productSimilarityBars` had existed and been tested since the chat-side card was built; only the
 * journey path ignored them.
 */
function toGroupProduct(
  product: WireProduct,
  seller: string,
  position: number,
): FormatGroupProduct {
  const { id } = productFormatInfo(product);
  const price = productPriceLabel(product);
  const facts = productFacts(product);
  const reach = productReach(product);
  const similarity = productSimilarityBars(product);
  const relevance = productRelevance(product);
  const cpm = productCpm(product);
  return {
    key: `${seller}#${product.product_id ?? 'anon'}#${position}`,
    title: productTitle(product),
    seller,
    ...(id ? { formatId: id } : {}),
    ...(price ? { price } : {}),
    // Empty collapses to absent, so a row with nothing to add carries no empty array for a renderer
    // to have to special-case.
    ...(facts.length > 0 ? { facts } : {}),
    ...(reach ? { reach } : {}),
    ...(similarity.length > 0 ? { similarity } : {}),
    ...(relevance !== null ? { relevance } : {}),
    ...(cpm !== null ? { cpm: cpm.value } : {}),
    ...(cpm?.currency ? { currency: cpm.currency } : {}),
  };
}

/**
 * Build the Discover panel's data from a recorded fan-out payload.
 *
 * Returns null when the payload is not a fan-out, so the caller reports an absence rather than
 * rendering an empty panel that looks like a real, empty answer.
 */
export function deriveDiscover(output: unknown): DiscoverData | null {
  const entries = parseFanOut(output);
  if (!entries) return null;

  const buckets = new Map<FormatKey, Bucket>();
  let unmatched = 0;
  let responded = 0;
  let errored = 0;

  for (const entry of entries) {
    if (sellerEntryFailed(entry)) {
      errored += 1;
      continue;
    }
    responded += 1;

    const response = entry.response;
    const products = Array.isArray(response?.products) ? response.products : [];
    const listTruncation = readListTruncation(response, 'products');
    const name = sellerEntryName(entry);

    products.forEach((product, position) => {
      const key = formatKeyFor(product);
      if (!key) {
        // Reported, never dropped. A product whose format matched nothing is still a product the
        // seller returned, and silently discarding it would understate the answer.
        unmatched += 1;
        return;
      }
      const bucket = buckets.get(key) ?? { products: [], sellers: [] };
      bucket.products.push(toGroupProduct(product, name, position));
      if (!bucket.sellers.includes(name)) bucket.sellers.push(name);
      if (listTruncation) bucket.truncation = listTruncation;
      buckets.set(key, bucket);
    });
  }

  // Authored order, so the panel's groups appear as the prototype lays them out rather than in
  // whatever order the sellers happened to answer.
  const groups: FormatGroup[] = [];
  for (const key of FORMAT_KEYS) {
    const bucket = buckets.get(key);
    if (!bucket || bucket.products.length === 0) continue;
    const meta = FORMAT_META[key];
    groups.push({
      formatKey: key,
      label: meta.label,
      cssVar: meta.cssVar,
      products: bucket.products,
      count: bucket.products.length,
      attribution: bucket.sellers,
      ...(bucket.truncation ? { truncation: bucket.truncation } : {}),
    });
  }

  const sellersQueried = entries.length;
  return { groups, unmatched, sellersQueried, sellersResponded: responded, sellersErrored: errored };
}

/**
 * The note under the dot grid.
 *
 * Built from real counts only. Says how many products across how many formats, and reports errored
 * sellers and unmatched products rather than quietly excluding them from the total.
 */
export function discoverNote(data: DiscoverData): string {
  const total = data.groups.reduce((sum, group) => sum + group.count, 0);
  const parts: string[] = [
    `${total} product${total === 1 ? '' : 's'} across ${data.groups.length} format${
      data.groups.length === 1 ? '' : 's'
    }`,
  ];
  parts.push(
    `${data.sellersResponded} of ${data.sellersQueried} sales agent${
      data.sellersQueried === 1 ? '' : 's'
    } responded`,
  );
  if (data.sellersErrored > 0) {
    parts.push(`${data.sellersErrored} errored`);
  }
  if (data.unmatched > 0) {
    parts.push(`${data.unmatched} in other formats`);
  }
  const trimmed = data.groups.filter((g) => g.truncation !== undefined).length;
  if (trimmed > 0) {
    parts.push(`${trimmed} list${trimmed === 1 ? '' : 's'} trimmed when recorded`);
  }
  return parts.join(' \u00b7 ');
}

/** The seller entries of a fan-out, or null when the payload is not one. */
export function fanOutEntries(output: unknown): SellerEntry[] | null {
  return parseFanOut(output);
}
