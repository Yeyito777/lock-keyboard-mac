/* Drives the Caps Lock LED without touching Caps Lock itself.
 * Karabiner mirrors its virtual keyboard's LED onto the keyboards it grabs, so
 * forcing that LED through the keyboard filter's HIDCapsLockLED property lights
 * the physical LED while typing (and the macOS Caps Lock badge) stay normal.
 * clang -O2 -Wall -Wextra caps-led.c -framework IOKit \
 *       -framework CoreFoundation -o caps-led
 */
#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/hid/IOHIDKeys.h>
#include <IOKit/hidsystem/IOHIDEventSystemClient.h>
#include <IOKit/hidsystem/IOHIDServiceClient.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

/* Exported by IOKit but not declared publicly. Passive clients receive no
 * events, so they need no Input Monitoring; Simple clients cannot set this. */
extern IOHIDEventSystemClientRef IOHIDEventSystemClientCreateWithType(
    CFAllocatorRef allocator, int type, CFDictionaryRef attributes);
enum { kPassiveClientType = 2 };

static bool has_prefix(CFTypeRef value, CFStringRef prefix)
{
    return value && CFGetTypeID(value) == CFStringGetTypeID() &&
           CFStringHasPrefix(value, prefix);
}

/* Same identification Karabiner uses for its own virtual devices. */
static bool is_karabiner_keyboard(IOHIDServiceClientRef service)
{
    CFTypeRef manufacturer = IOHIDServiceClientCopyProperty(service, CFSTR(kIOHIDManufacturerKey));
    CFTypeRef product = IOHIDServiceClientCopyProperty(service, CFSTR(kIOHIDProductKey));
    bool match = has_prefix(manufacturer, CFSTR("pqrs.org")) &&
                 has_prefix(product, CFSTR("Karabiner DriverKit VirtualHIDKeyboard"));
    if (manufacturer)
        CFRelease(manufacturer);
    if (product)
        CFRelease(product);
    return match;
}

int main(int argc, char **argv)
{
    if (argc < 2 || (strcmp(argv[1], "on") && strcmp(argv[1], "off") &&
                     strcmp(argv[1], "status"))) {
        fprintf(stderr, "Usage: caps-led on|off|status [COMMAND [ARG...]]\n"
                        "  on: force the LED on; off: LED follows Caps Lock again.\n"
                        "  COMMAND runs afterwards in this process (exec).\n");
        return 2;
    }
    /* "Auto" hands the LED back to the real Caps Lock state. */
    CFStringRef value = !strcmp(argv[1], "on")  ? CFSTR("On")
                        : !strcmp(argv[1], "off") ? CFSTR("Auto")
                                                  : NULL;
    IOHIDEventSystemClientRef client =
        IOHIDEventSystemClientCreateWithType(kCFAllocatorDefault, kPassiveClientType, NULL);
    CFArrayRef services = client ? IOHIDEventSystemClientCopyServices(client) : NULL;
    int found = 0, failed = 0;
    bool lit = false;
    for (CFIndex i = 0; services && i < CFArrayGetCount(services); i++) {
        IOHIDServiceClientRef service =
            (IOHIDServiceClientRef)CFArrayGetValueAtIndex(services, i);
        if (!is_karabiner_keyboard(service))
            continue;
        found++;
        if (value && !IOHIDServiceClientSetProperty(service, CFSTR("HIDCapsLockLED"), value))
            failed++;
        CFTypeRef state = IOHIDServiceClientCopyProperty(service, CFSTR("HIDCapsLockLED"));
        lit |= state && CFEqual(state, CFSTR("On"));
        if (state)
            CFRelease(state);
    }
    if (services)
        CFRelease(services);
    if (client)
        CFRelease(client);

    int status = 0;
    if (!found) {
        fprintf(stderr, "caps-led: Karabiner virtual keyboard not found\n");
        status = 1;
    } else if (failed || (value && CFEqual(value, CFSTR("On")) && !lit)) {
        fprintf(stderr, "caps-led: could not set the Caps Lock LED\n");
        status = 1;
    } else if (!value) {
        printf("{\"led\":%s}\n", lit ? "true" : "false");
    }
    if (argc > 2) {
        /* Become the follow-up command (a notification) instead of spawning it:
         * Karabiner kills a still-running shell_command before starting the
         * next, so LED writes stay ordered and none are left orphaned. */
        fflush(NULL);
        execv(argv[2], argv + 2);
        perror("caps-led: exec");
        return 127;
    }
    return status;
}
