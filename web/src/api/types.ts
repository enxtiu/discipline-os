/**
 * API types.
 *
 * `src/api/generated/schema.d.ts` is produced by `npm run gen:api` from the
 * live OpenAPI schema (`openapi-typescript`) — it is the single source of truth
 * for every request/response shape. This module only *narrows* it where the
 * generator's output is technically correct but unusable:
 *
 * 1. `dict[str, Any]` columns (JSONB such as `notification_prefs`) become
 *    `{[k: string]: unknown}`; the UI knows what is inside them.
 * 2. A pydantic field with a default lands in OpenAPI as a *defaulted* property
 *    and `openapi-typescript` marks it required, even though the server is happy
 *    to receive the body with the key omitted. Request models are therefore
 *    `Partial<>` here.
 * 3. `SessionOut` / `SettingsEnvelopeOut` are re-pointed at those narrowed
 *    shapes so components get `settings.language: string` instead of `unknown`.
 *
 * Nothing else is redefined: add new aliases, never hand-written duplicates.
 */
import type { components as GeneratedComponents } from './generated/schema'

type Schemas = GeneratedComponents['schemas']

/** JSON-ish bag (JSONB columns the UI treats as opaque). */
export type JsonObject = Record<string, unknown>

// --------------------------------------------------------------------------- //
// Read models
// --------------------------------------------------------------------------- //
export type UserSummary = Schemas['UserSummaryOut']
export type Device = Schemas['DeviceOut']
export type DeviceList = Schemas['DeviceListOut']
export type PairingResult = Omit<Schemas['PairingResultOut'], 'settings'> & {
  settings: UserSettings
}
export type PairingCodeInfo = Schemas['PairingCodeInfoOut']
export type ApiToken = Schemas['ApiTokenOut']
export type ApiTokenIssued = Schemas['ApiTokenIssuedOut']
export type ApiTokenList = Schemas['ApiTokenListOut']
export type ScopeInfo = Schemas['ScopeOut']
export type BodyParams = Schemas['BodyParamsOut']
export type Health = Schemas['HealthOut']
export type AppConfig = Schemas['AppConfigOut']
export type VersionInfo = Schemas['VersionOut']
export type I18nMessage = Schemas['I18nMessageOut']

export type Targets = Omit<Schemas['TargetsOut'], 'warnings' | 'macros_kcal'> & {
  warnings: string[]
  macros_kcal: Record<string, number>
}

export type UserSettings = Omit<
  Schemas['UserSettingsOut'],
  'notification_prefs' | 'xp_weights'
> & {
  notification_prefs: Record<string, boolean>
  xp_weights: JsonObject
}

export type Session = Omit<Schemas['SessionOut'], 'settings' | 'targets'> & {
  settings: UserSettings
  targets: Targets
}

export type SettingsEnvelope = Omit<
  Schemas['SettingsEnvelopeOut'],
  'settings' | 'targets'
> & {
  settings: UserSettings
  targets: Targets
}

// --------------------------------------------------------------------------- //
// Write models — every field optional, the server applies its own defaults
// --------------------------------------------------------------------------- //
export type PairingRequest = Schemas['PairingRequestIn']
export type DeviceUpdate = Schemas['DeviceUpdateIn']
export type ApiTokenCreate = Schemas['ApiTokenCreateIn']

export type SettingsUpdate = Partial<
  Omit<Schemas['SettingsUpdateIn'], 'notification_prefs' | 'xp_weights'>
> & {
  notification_prefs?: Record<string, boolean>
  xp_weights?: JsonObject
}

export type TargetsInput = Partial<Schemas['TargetsIn']>
export type TargetPreview = Partial<Schemas['TargetPreviewIn']>

export type OnboardingPayload = Partial<
  Omit<Schemas['OnboardingIn'], 'notifications'>
> & {
  notifications?: Record<string, boolean>
}

/** `{error: {code, message, details}}` — every failure from the API. */
export interface ApiErrorBody {
  code: string
  message: string
  details?: Record<string, unknown> | null
}

// --------------------------------------------------------------------------- //
// Nutrition (stage 2)
// --------------------------------------------------------------------------- //
export type Food = Schemas['FoodOut']
export type FoodMatch = Schemas['FoodMatchOut']
export type FoodSearch = Schemas['FoodSearchOut']
export type Favorite = Schemas['FavoriteOut']
export type Recent = Schemas['RecentOut']
export type RecentList = Schemas['RecentListOut']
export type Recipe = Schemas['RecipeOut']
export type RecipeBrief = Schemas['RecipeBriefOut']
export type RecipeItem = Schemas['RecipeItemOut']
export type RecipeList = Schemas['RecipeListOut']
export type MealLog = Schemas['MealLogOut']
export type MealItem = Schemas['MealItemOut']
export type DaySummary = Schemas['DaySummaryOut']
export type WaterLog = Schemas['WaterLogOut']
export type Macros = Schemas['MacrosOut']

export type MealType = 'breakfast' | 'lunch' | 'dinner' | 'snack' | 'other'
export type MatchMethod = 'exact' | 'fts' | 'trgm' | 'barcode' | 'manual' | 'ai' | 'none'

/** The API fills defaults server-side, so writes carry only what the user set. */
export interface MealItemIn {
  food_id?: string
  recipe_id?: string
  grams: number
  quantity?: number
  unit?: string
  match_method?: MatchMethod
  name?: string
  kcal_per_100g?: number
  protein_per_100g?: number
  fat_per_100g?: number
  carbs_per_100g?: number
  fiber_per_100g?: number
}

export interface MealLogIn {
  id?: string
  logged_at?: string
  meal_type?: MealType
  source?: 'search' | 'favorite' | 'recent' | 'recipe' | 'copy' | 'manual' | 'barcode'
  title?: string
  notes?: string
  items: MealItemIn[]
}

export interface RecipeItemIn {
  food_id: string
  grams: number
  note?: string
}

export interface RecipeIn {
  name: string
  description?: string
  servings?: number
  cooked_weight_g?: number
  items: RecipeItemIn[]
}

export interface FoodIn {
  name_ru?: string
  name_en?: string
  brand?: string
  barcode?: string
  category?: string
  kcal_per_100g: number
  protein_per_100g?: number
  fat_per_100g?: number
  carbs_per_100g?: number
  fiber_per_100g?: number
  default_serving_g?: number
  default_serving_label?: string
  is_liquid?: boolean
}
