"use server";

import { redirect } from "next/navigation";

import { signIn, signOut } from "@/lib/auth";

export async function login(_state: string | null, form: FormData): Promise<string | null> {
  const password = form.get("password");
  if (typeof password !== "string" || !(await signIn(password))) {
    return "That password isn't right.";
  }
  redirect("/experiments");
}

export async function logout(): Promise<void> {
  await signOut();
  redirect("/login");
}
