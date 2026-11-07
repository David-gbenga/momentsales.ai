import { render } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactElement } from "react";

import messages from "@/messages/en-GB.json";

export function renderWithIntl(ui: ReactElement) {
  return render(
    <NextIntlClientProvider locale="en-GB" messages={messages}>
      {ui}
    </NextIntlClientProvider>,
  );
}
