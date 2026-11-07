import Link from "next/link";

/** Simple centred page frame for auth and link-landing pages. */
export function Centered({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mx-auto flex min-h-dvh max-w-md flex-col px-4 py-10">
      <Link href="/" className="text-lg font-semibold tracking-tight">
        momentsales.ai
      </Link>
      <main id="main" className="mt-10 grid gap-6">
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {children}
      </main>
    </div>
  );
}
