import random
import time
from playwright.sync_api import sync_playwright

def human_move(page, start_x, start_y, end_x, end_y, duration=1.2):
    """Generates non-linear mouse paths simulating physical muscle movements."""
    steps = int(duration * 25)
    for i in range(1, steps + 1):
        progress = i / steps
        if progress < 0.5:
            ease = 2 * progress * progress
        else:
            ease = -1 + (4 - 2 * progress) * progress
            
        jitter_x = random.uniform(-0.8, 0.8)
        jitter_y = random.uniform(-0.8, 0.8)
        
        current_x = start_x + (end_x - start_x) * ease + jitter_x
        current_y = start_y + (end_y - start_y) * ease + jitter_y
        
        page.mouse.move(current_x, current_y)
        time.sleep(duration / steps)

def run():
    with sync_playwright() as p:
        print("Connecting to your authentic running Brave instance...")
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        
        context = browser.contexts[0]
        page = context.pages[0]
        
        print("Navigating to target location directory...")
        page.goto("https://www.robertwalters.de/jobs.html")
        
        # Let the verification layout render completely
        page.wait_for_timeout(5000)
        
        try:
            # DYNAMIC FIX: Ask the browser context directly where the container iframe sits on your current window layout
            print("Extracting dynamic coordinate positions from browser window...")
            iframe_geometry = page.evaluate("""() => {
                const iframe = document.querySelector('iframe[src="about:blank"]');
                if (iframe) {
                    const rect = iframe.getBoundingClientRect();
                    return { x: rect.left, y: rect.top, width: rect.width, height: rect.height };
                }
                return null;
            }""")
            
            if iframe_geometry:
                # Target the visual center point of the security container frame
                target_x = iframe_geometry["x"] + (iframe_geometry["width"] / 2) + random.uniform(-2, 2)
                target_y = iframe_geometry["y"] + (iframe_geometry["height"] / 2) + random.uniform(-2, 2)
                print(f"Target securely mapped to active window space at: ({target_x}, {target_y})")
            else:
                # If window layout queries fail, adapt fallback parameters for the snapped split-screen view
                target_x = 1450 + random.uniform(-10, 10)
                target_y = 480 + random.uniform(-5, 5)
                print(f"Iframe hidden. Using split-screen layout safety fallback: ({target_x}, {target_y})")
            
            # Position cursor organically before starting interaction sequence
            start_x = random.uniform(100, 250)
            start_y = random.uniform(100, 250)
            page.mouse.move(start_x, start_y)
            time.sleep(0.4)
            
            print("Executing humanized cursor movement trajectory...")
            human_move(page, start_x, start_y, target_x, target_y, duration=1.4)
            time.sleep(random.uniform(0.3, 0.5))
            
            print("Engaging physical long hold validation sequence...")
            page.mouse.down()
            
            start_time = time.time()
            while time.time() - start_time < 13.0:
                page.mouse.move(target_x + random.uniform(-1, 1), target_y + random.uniform(-1, 1))
                time.sleep(random.uniform(0.05, 0.12))
                
            page.mouse.up()
            print("Hold interaction completed successfully!")
            
        except Exception as e:
            print(f"Script runtime exception encountered: {e}")
            
        time.sleep(10)

if __name__ == "__main__":
    run()