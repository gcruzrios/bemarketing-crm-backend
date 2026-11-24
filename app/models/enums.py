import enum


class UserRole(str, enum.Enum):
    admin = "admin"
    manager = "manager"
    sales = "sales"
    accounting = "accounting"


class CompanyType(str, enum.Enum):
    hotel = "hotel"
    restaurant = "restaurant"
    cafe = "cafe"
    tour_operator = "tour_operator"
    other = "other"


class LeadSource(str, enum.Enum):
    whatsapp = "whatsapp"
    email = "email"
    website = "website"
    referral = "referral"
    phone = "phone"
    social = "social"
    other = "other"


class LeadStatus(str, enum.Enum):
    new = "new"
    contacted = "contacted"
    qualified = "qualified"
    unqualified = "unqualified"
    converted = "converted"
    lost = "lost"


class EnrichmentStatus(str, enum.Enum):
    incomplete = "incomplete"
    complete = "complete"
    needs_review = "needs_review"


class ActorType(str, enum.Enum):
    user = "user"
    agent = "agent"
    system = "system"


class IngestStatus(str, enum.Enum):
    processed = "processed"
    rejected = "rejected"


class ServiceType(str, enum.Enum):
    direct_bookings = "direct_bookings"
    social_media = "social_media"
    content_video = "content_video"
    seo_geo = "seo_geo"
    web_design = "web_design"
    consulting = "consulting"
    other = "other"


class Currency(str, enum.Enum):
    CRC = "CRC"
    USD = "USD"


class BillingType(str, enum.Enum):
    one_time = "one_time"
    monthly = "monthly"
    hourly = "hourly"
    custom = "custom"


class OpportunityStatus(str, enum.Enum):
    open = "open"
    won = "won"
    lost = "lost"


class QuoteStatus(str, enum.Enum):
    draft = "draft"
    sent = "sent"
    viewed = "viewed"
    accepted = "accepted"
    rejected = "rejected"
    expired = "expired"
    cancelled = "cancelled"


class InvoiceStatus(str, enum.Enum):
    draft = "draft"
    issued = "issued"
    sent = "sent"
    partial = "partial"
    paid = "paid"
    overdue = "overdue"
    cancelled = "cancelled"


class ActivityType(str, enum.Enum):
    call = "call"
    email = "email"
    whatsapp = "whatsapp"
    meeting = "meeting"
    task = "task"
    note = "note"
    follow_up = "follow_up"


class ActivityStatus(str, enum.Enum):
    pending = "pending"
    completed = "completed"
    cancelled = "cancelled"
