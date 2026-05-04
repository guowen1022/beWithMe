declare global {
  interface Window {
    beWithMeBridge?: {
      browser: {
        navigate(url: string): Promise<void>;
        hide(): Promise<void>;
        setBounds(rect: {
          x: number;
          y: number;
          width: number;
          height: number;
        }): Promise<void>;
        back(): Promise<void>;
        forward(): Promise<void>;
        reload(): Promise<void>;
        getCurrentUrl(): Promise<string | null>;
        onUrlChange(
          cb: (p: { url: string; title: string }) => void,
        ): () => void;
        onSelectionChange(
          cb: (p: { text: string; url: string; title: string }) => void,
        ): () => void;
        onLoadingChange(cb: (p: { loading: boolean }) => void): () => void;
        onScrollChange(
          cb: (p: {
            url: string;
            title: string;
            scroll_y: number;
            scroll_height: number;
            viewport_text: string;
          }) => void,
        ): () => void;
      };
    };
  }
}

export {};
