import { createClient, type SupabaseClient } from "@supabase/supabase-js";

const authMode = import.meta.env.VITE_AUTH_MODE === "supabase";
const projectUrl = import.meta.env.VITE_SUPABASE_URL as string | undefined;
const publishableKey = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY as string | undefined;

export const supabaseAuthEnabled = authMode;
export const supabaseAuthConfigured = Boolean(projectUrl && publishableKey);

export const supabase: SupabaseClient | null = supabaseAuthConfigured
  ? createClient(projectUrl!, publishableKey!, {
      auth: {
        autoRefreshToken: true,
        detectSessionInUrl: true,
        persistSession: true,
      },
    })
  : null;

export async function getSupabaseAccessToken(): Promise<string | null> {
  if (!supabaseAuthEnabled || !supabase) return null;
  const { data, error } = await supabase.auth.getSession();
  if (error) throw error;
  return data.session?.access_token ?? null;
}
