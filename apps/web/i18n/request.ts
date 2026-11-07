import { getRequestConfig } from "next-intl/server";

// en-GB only at launch; message keys are ready for more locales (PRD i18n).
export const LOCALE = "en-GB";

export default getRequestConfig(async () => ({
  locale: LOCALE,
  timeZone: "Europe/London",
  messages: (await import(`../messages/${LOCALE}.json`)).default,
}));
